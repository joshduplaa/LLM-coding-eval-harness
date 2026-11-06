"""Command-line interface.

evalharness list-tasks
evalharness validate [--task ID ...]
evalharness run --model mock-reference [--task ID ...] [--samples N] [--seed S]
evalharness runs                      # runs stored in the results database
evalharness report RUN_ID             # pass rates with sample sizes and 95% intervals
evalharness import runs/*.json        # add saved run files to the database
evalharness serve                     # read-only HTTP API
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .errors import HarnessError
from .metrics import summarize_samples
from .models import MOCK_BEHAVIORS, GenerationParams, make_model
from .runner import evaluate, validate_task
from .sandbox import DEFAULT_BACKEND, DEFAULT_IMAGE, DEFAULT_RUNTIME, SandboxLimits
from .store import DEFAULT_DB, ResultsStore
from .tasks import DEFAULT_TASKS_DIR, load_tasks

MOCK_BANNER = "*** MOCK MODEL — synthetic output, NOT a real model result ***"


def _sandbox_label(runtime: str) -> str:
    backend = os.environ.get("EVALHARNESS_SANDBOX_BACKEND", DEFAULT_BACKEND)
    return f"docker/{runtime}" if backend == "docker" else backend


def _tasks_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("EVALHARNESS_TASKS_DIR") or DEFAULT_TASKS_DIR)


def cmd_list(args) -> int:
    for t in load_tasks(_tasks_dir(args.tasks_dir)):
        print(f"{t.id:24} v{t.version:8} {t.title}  [{t.content_hash[:12]}]")
    return 0


def cmd_validate(args) -> int:
    tasks = load_tasks(_tasks_dir(args.tasks_dir), args.task)
    bad = 0
    for t in tasks:
        v = validate_task(t, SandboxLimits(), args.image, args.runtime)
        print(f"{'OK  ' if v.ok else 'FAIL'} {t.id} ({v.expected_tests} tests)")
        for c in v.checks:
            mark = "ok" if c["ok"] else "UNEXPECTED"
            print(f"     {c['solution']:24} expect {c['expect']:7} got {c['category']:18} {mark}")
        bad += not v.ok
    return 1 if bad else 0


def cmd_run(args) -> int:
    tasks = load_tasks(_tasks_dir(args.tasks_dir), args.task)
    model = make_model(args.model, tasks)
    params = GenerationParams(temperature=args.temperature, seed=args.seed)
    if model.is_mock:
        print(MOCK_BANNER)
    print(
        f"model={model.model_id} tasks={len(tasks)} samples/task={args.samples} "
        f"sandbox={_sandbox_label(args.runtime)}"
    )

    def show(rec: dict) -> None:
        r = rec["result"]
        print(
            f"  {rec['task']['id']:24} #{rec['sample_index']:<3} {r['category']:18} "
            f"{r['tests_passed']}/{r['tests_total']} tests  {rec['sandbox']['duration_s']:.2f}s"
        )

    run = evaluate(
        tasks,
        model,
        params,
        args.samples,
        SandboxLimits(),
        args.image,
        on_sample=show,
        runtime=args.runtime,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    ResultsStore(args.db).save_run(run)

    print()
    print_report(
        run["run_id"], run["model"]["id"], run["is_mock"], summarize_samples(run["samples"])
    )
    print(f"run record: {path}")
    print(f"stored in:  {args.db} (run_id {run['run_id']})")
    return 0


def _pct(x: float | None) -> str:
    return "   n/a" if x is None else f"{100 * x:5.1f}%"


def _row(label: str, s: dict) -> str:
    ci = "" if s["ci_low"] is None else f"[{_pct(s['ci_low'])}, {_pct(s['ci_high'])}]"
    others = ", ".join(
        f"{k}={v}" for k, v in s["counts"].items() if v and k not in ("passed", "harness_error")
    )
    return (
        f"  {label:24} {s['passed']:>4}/{s['n_scored']:<4} {_pct(s['pass_rate'])}  {ci:18} "
        f"excluded={s['n_excluded']:<3} {others}"
    )


def print_report(run_id: str, model_id: str, is_mock: bool, m: dict) -> None:
    if is_mock:
        print(MOCK_BANNER)
    print(f"run {run_id}  model={model_id}")
    print(f"  {'task':24} {'pass/n':>9} {'rate':>6}  {'95% CI (Wilson)':18} other outcomes")
    for task_id, s in m["by_task"].items():
        print(_row(task_id, s))
    print(_row("OVERALL", m["overall"]))
    for label, s in [("overall", m["overall"]), *m["by_task"].items()]:
        if s["warning"]:
            print(f"  WARNING ({label}): {s['warning']}")
    print(f"  note: n excludes harness errors. {m['note']}")
    if is_mock:
        print(MOCK_BANNER)


def cmd_runs(args) -> int:
    rows = ResultsStore(args.db).list_runs()
    if not rows:
        print(f"no runs in {args.db}")
    for r in rows:
        mock = "MOCK " if r["is_mock"] else "     "
        print(
            f"{r['run_id']}  {mock}{r['model_id']:24} {r['n_samples']:>4} samples  "
            f"sandbox={r['sandbox_runtime'] or 'kubernetes'}  {r['started_at']}"
        )
    return 0


def cmd_report(args) -> int:
    store = ResultsStore(args.db)
    doc = store.get_run(args.run_id)
    if doc is None:
        raise HarnessError(f"no run {args.run_id!r} in {args.db}")
    m = summarize_samples(store.sample_results(args.run_id))
    if args.json:
        print(json.dumps({"run_id": args.run_id, "is_mock": doc["is_mock"], **m}, indent=2))
    else:
        print_report(args.run_id, doc["model"]["id"], doc["is_mock"], m)
    return 0


def cmd_import(args) -> int:
    store = ResultsStore(args.db)
    for f in args.files:
        run = json.loads(Path(f).read_text(encoding="utf-8"))
        store.save_run(run)
        print(f"imported {run['run_id']} ({len(run['samples'])} samples)")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(args.db, _tasks_dir(args.tasks_dir)), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evalharness", description=__doc__.split("\n")[0])
    p.add_argument("--tasks-dir", help="directory of tasks (default: ./tasks)")
    p.add_argument("--image", default=DEFAULT_IMAGE, help="sandbox image tag")
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"results database (default: {DEFAULT_DB}, or $EVALHARNESS_DB)",
    )
    p.add_argument(
        "--runtime",
        default=DEFAULT_RUNTIME,
        help="OCI runtime for sandboxes: runc (default) or runsc (gVisor, if installed)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-tasks", help="list available tasks").set_defaults(fn=cmd_list)

    v = sub.add_parser("validate", help="check tests pass the reference and fail incorrect ones")
    v.add_argument("--task", action="append", help="task id (repeatable; default: all)")
    v.set_defaults(fn=cmd_validate)

    r = sub.add_parser("run", help="run an evaluation end to end")
    r.add_argument(
        "--model",
        required=True,
        help=f"model spec; available: {', '.join('mock-' + b for b in MOCK_BEHAVIORS)}",
    )
    r.add_argument("--task", action="append", help="task id (repeatable; default: all)")
    r.add_argument("--samples", type=int, default=1, help="samples per task")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--out", default="runs", help="directory for run records")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("runs", help="list stored runs").set_defaults(fn=cmd_runs)

    rp = sub.add_parser("report", help="pass rates with sample sizes and 95% intervals")
    rp.add_argument("run_id")
    rp.add_argument("--json", action="store_true", help="machine-readable output")
    rp.set_defaults(fn=cmd_report)

    im = sub.add_parser("import", help="add saved run JSON files to the database")
    im.add_argument("files", nargs="+")
    im.set_defaults(fn=cmd_import)

    sv = sub.add_parser("serve", help="read-only HTTP API over the results database")
    sv.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (HarnessError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
