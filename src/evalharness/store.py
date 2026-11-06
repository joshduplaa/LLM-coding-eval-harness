"""SQLite results store.

Each run is stored twice on purpose:
- The full run document and each full sample record are stored as JSON text. That keeps
  everything needed to reproduce a sample, even fields added later, with no schema change.
- A few fields are copied into indexed columns (model, task, category, ...) so they can be
  filtered and counted with plain SQL.

Writes are append-only: a run is inserted once, in one transaction, and never updated.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import HarnessError

SCHEMA_VERSION = 1
DEFAULT_DB = Path(os.environ.get("EVALHARNESS_DB", "results/evalharness.sqlite"))

_SCHEMA = """
CREATE TABLE runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    model_id          TEXT NOT NULL,
    is_mock           INTEGER NOT NULL CHECK (is_mock IN (0, 1)),
    sandbox_runtime   TEXT,
    samples_per_task  INTEGER NOT NULL,
    harness_version   TEXT,
    git_commit        TEXT,
    doc               TEXT NOT NULL   -- run document without samples (includes validations)
);
CREATE TABLE samples (
    run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    task_id       TEXT NOT NULL,
    sample_index  INTEGER NOT NULL,
    task_version  TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    is_mock       INTEGER NOT NULL CHECK (is_mock IN (0, 1)),
    category      TEXT NOT NULL,
    detail        TEXT,
    tests_passed  INTEGER,
    tests_total   INTEGER,
    duration_s    REAL,
    record        TEXT NOT NULL,  -- the complete sample record
    PRIMARY KEY (run_id, task_id, sample_index)
);
CREATE INDEX samples_by_category ON samples (run_id, category);
"""

SAMPLE_COLUMNS = (
    "run_id, task_id, sample_index, task_version, content_hash, model_id, is_mock, "
    "category, detail, tests_passed, tests_total, duration_s"
)


class ResultsStore:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript(_SCHEMA)
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise HarnessError(
                    f"{self.path} has schema version {version}; this harness expects "
                    f"{SCHEMA_VERSION}"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation: simple, and safe across API worker threads.
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:  # commits on success, rolls back on any exception
                yield db
        finally:
            db.close()

    def save_run(self, run: dict) -> None:
        doc = {k: v for k, v in run.items() if k != "samples"}
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run["run_id"],
                        run["started_at"],
                        run.get("finished_at"),
                        run["model"]["id"],
                        int(bool(run["is_mock"])),
                        run.get("sandbox_runtime"),
                        run["samples_per_task"],
                        run["harness"].get("version"),
                        run["harness"].get("git_commit"),
                        json.dumps(doc),
                    ),
                )
                db.executemany(
                    f"INSERT INTO samples ({SAMPLE_COLUMNS}, record) "  # noqa: S608 - constant
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            run["run_id"],
                            s["task"]["id"],
                            s["sample_index"],
                            s["task"]["version"],
                            s["task"]["content_hash"],
                            s["model_id"],
                            int(bool(s["is_mock"])),
                            s["result"]["category"],
                            s["result"].get("detail"),
                            s["result"].get("tests_passed"),
                            s["result"].get("tests_total"),
                            s.get("sandbox", {}).get("duration_s"),
                            json.dumps(s),
                        )
                        for s in run["samples"]
                    ],
                )
        except sqlite3.IntegrityError as e:
            raise HarnessError(f"could not store run {run.get('run_id')!r}: {e}") from e

    def list_runs(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT r.run_id, r.started_at, r.finished_at, r.model_id, r.is_mock, "
                "r.sandbox_runtime, r.samples_per_task, COUNT(s.run_id) AS n_samples "
                "FROM runs r LEFT JOIN samples s USING (run_id) "
                "GROUP BY r.run_id ORDER BY r.started_at DESC, r.run_id DESC"
            ).fetchall()
        return [_bools(dict(r)) for r in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT doc FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(row["doc"]) if row else None

    def sample_rows(
        self,
        run_id: str,
        task_id: str | None = None,
        category: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict]:
        """Indexed columns only (no prompt, code, or test output). Filters are optional."""
        # Only the constant column list is interpolated; every value is a ? parameter.
        sql = f"SELECT {SAMPLE_COLUMNS} FROM samples WHERE run_id = ?"  # noqa: S608
        args: list = [run_id]
        if task_id is not None:
            sql += " AND task_id = ?"
            args.append(task_id)
        if category is not None:
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY task_id, sample_index LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._connect() as db:
            return [_bools(dict(r)) for r in db.execute(sql, args).fetchall()]

    def get_sample(self, run_id: str, task_id: str, sample_index: int) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT record FROM samples WHERE run_id = ? AND task_id = ? AND sample_index = ?",
                (run_id, task_id, sample_index),
            ).fetchone()
        return json.loads(row["record"]) if row else None

    def sample_results(self, run_id: str) -> list[dict]:
        """The minimum `metrics.summarize_samples` needs, without loading full records."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT task_id, category FROM samples WHERE run_id = ?", (run_id,)
            ).fetchall()
        return [{"task": {"id": r["task_id"]}, "result": {"category": r["category"]}} for r in rows]


def _bools(row: dict) -> dict:
    if "is_mock" in row:
        row["is_mock"] = bool(row["is_mock"])
    return row
