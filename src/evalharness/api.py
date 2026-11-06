"""Read-only HTTP API over the results store.

    uv run evalharness serve            # http://127.0.0.1:8000/docs for interactive docs

Read-only on purpose: starting evaluations over HTTP means the service starts sandboxes, and
how it does that safely is a Milestone 4 decision. Every response about a mock run carries the
MOCK notice, so a mock result can't be shown on its own without its label.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from prometheus_client import make_asgi_app

from . import __version__
from .metrics import summarize_samples
from .prom import build_registry
from .scoring import Category
from .store import DEFAULT_DB, ResultsStore
from .tasks import DEFAULT_TASKS_DIR, load_tasks

MOCK_NOTICE = "MOCK MODEL — synthetic output, NOT a real model result"


def _label(body: dict, is_mock: bool) -> dict:
    return {"is_mock": is_mock, "notice": MOCK_NOTICE if is_mock else None, **body}


def create_app(db_path: str | Path = DEFAULT_DB, tasks_dir: Path = DEFAULT_TASKS_DIR) -> FastAPI:
    store = ResultsStore(db_path)
    app = FastAPI(
        title="evalharness",
        version=__version__,
        description="Read-only access to evaluation runs, samples, and pass-rate metrics.",
    )

    # Prometheus scrape endpoint. Read-only, like the rest of the API.
    app.mount("/metrics", make_asgi_app(registry=build_registry(store)))

    def run_or_404(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        return run

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/tasks")
    def tasks() -> list[dict]:
        return [
            {"id": t.id, "version": t.version, "title": t.title, "content_hash": t.content_hash}
            for t in load_tasks(tasks_dir)
        ]

    @app.get("/runs")
    def runs() -> list[dict]:
        return [_label(r, r["is_mock"]) for r in store.list_runs()]

    @app.get("/runs/{run_id}")
    def run(run_id: str) -> dict:
        doc = run_or_404(run_id)
        return _label(doc, doc["is_mock"])

    @app.get("/runs/{run_id}/metrics")
    def metrics(run_id: str) -> dict:
        doc = run_or_404(run_id)
        body = {"run_id": run_id, "model_id": doc["model"]["id"]}
        body.update(summarize_samples(store.sample_results(run_id)))
        return _label(body, doc["is_mock"])

    @app.get("/runs/{run_id}/samples")
    def samples(
        run_id: str,
        task: str | None = None,
        category: Category | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        doc = run_or_404(run_id)
        rows = store.sample_rows(
            run_id, task, category.value if category else None, limit=limit, offset=offset
        )
        return _label({"run_id": run_id, "samples": rows}, doc["is_mock"])

    @app.get("/runs/{run_id}/samples/{task_id}/{sample_index}")
    def sample(run_id: str, task_id: str, sample_index: int) -> dict:
        doc = run_or_404(run_id)
        record = store.get_sample(run_id, task_id, sample_index)
        if record is None:
            raise HTTPException(status_code=404, detail="no such sample")
        return _label({"run_id": run_id, "record": record}, doc["is_mock"])

    return app
