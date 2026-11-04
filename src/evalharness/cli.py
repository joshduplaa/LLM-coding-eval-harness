"""Command-line interface.

evalharness list-tasks
evalharness validate [--task ID ...]
evalharness run --model mock-reference [--task ID ...] [--samples N] [--seed S]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .errors import HarnessError
from .models import MOCK_BEHAVIORS, GenerationParams, make_model
from .runner import evaluate, validate_task
from .sandbox import DEFAULT_IMAGE, SandboxLimits
from .tasks import DEFAULT_TASKS_DIR, load_tasks

MOCK_BANNER = "*** MOCK MODEL — synthetic output, NOT a real model result ***"


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
        v = validate_task(t, SandboxLimits(), args.image)
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
    print(f"model={model.model_id} tasks={len(tasks)} samples/task={args.samples}")

    def show(rec: dict) -> None:
        r = rec["result"]
        print(
            f"  {rec['task']['id']:24} #{rec['sample_index']:<3} {r['category']:18} "
            f"{r['tests_passed']}/{r['tests_total']} tests  {rec['sandbox']['duration_s']:.2f}s"
        )

    run = evaluate(tasks, model, params, args.samples, SandboxLimits(), args.image, on_sample=show)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")

    counts = Counter(s["result"]["category"] for s in run["samples"])
    n = len(run["samples"])
    print(f"\nresults ({n} samples): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if model.is_mock:
        print(MOCK_BANNER)
    print(f"run record: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evalharness", description=__doc__.split("\n")[0])
    p.add_argument("--tasks-dir", help="directory of tasks (default: ./tasks)")
    p.add_argument("--image", default=DEFAULT_IMAGE, help="sandbox image tag")
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
