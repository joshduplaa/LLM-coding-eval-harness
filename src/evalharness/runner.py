"""Orchestration: validate a task, generate samples, run them in the sandbox, build records.

Every record carries what you need to reproduce it: the harness version and git commit, the task
id, version and content hash, the model id and parameters, the exact prompt and prompt-template
version, the raw completion, the extracted code, the sandbox image ID and limits, the test output,
the exit status, and the duration.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .errors import TaskValidationError
from .models import GenerationParams, Model
from .prompts import PROMPT_TEMPLATE_VERSION, build_prompt, extract_code
from .sandbox import (
    DEFAULT_IMAGE,
    DEFAULT_RUNTIME,
    SandboxLimits,
    SandboxOutcome,
    run_in_sandbox,
)
from .scoring import Category, Verdict, classify
from .tasks import Task

_REPO = Path(__file__).resolve().parents[2]


def harness_info() -> dict:
    commit, dirty = None, None
    try:
        commit = subprocess.run(
            ["git", "-C", str(_REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(_REPO), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "version": __version__,
        "git_commit": commit,
        "git_dirty": dirty,
        "python": platform.python_version(),
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    }


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def task_files(task: Task, solution_code: str) -> dict[str, str]:
    return {"solution.py": solution_code, **task.test_files}


def task_limits(task: Task, base: SandboxLimits) -> SandboxLimits:
    return SandboxLimits(**{**base.to_dict(), "timeout_s": task.timeout_s})


def _outcome_summary(o: SandboxOutcome) -> dict:
    env = o.envelope or {}
    return {
        "image": o.image,
        "image_id": o.image_id,
        "backend": o.backend,
        "runtime": o.runtime,
        "runtime_used": o.runtime_used,
        "pids_limit_applied": o.pids_limit_applied,
        "limits": o.limits,
        "docker_returncode": o.docker_returncode,
        "test_returncode": env.get("returncode"),
        "host_timed_out": o.host_timed_out,
        "oom_killed": o.oom_killed,
        "output_exceeded": o.output_exceeded,
        "envelope_problem": o.envelope_problem,
        "duration_s": o.duration_s,
        "test_output": env.get("log_tail", ""),
        "report": env.get("report"),
        "stderr_tail": o.stderr_tail,
    }


@dataclass
class ValidationResult:
    task_id: str
    ok: bool
    expected_tests: int
    checks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "ok": self.ok,
            "expected_tests": self.expected_tests,
            "checks": self.checks,
        }


def validate_task(
    task: Task, limits: SandboxLimits, image: str = DEFAULT_IMAGE, runtime: str = DEFAULT_RUNTIME
) -> ValidationResult:
    """The reference solution must pass, and every incorrect solution must fail on the merits.

    "On the merits" means wrong_answer or runtime_error. A harness error or timeout on an
    incorrect solution doesn't prove the tests can catch it.
    """
    lim = task_limits(task, limits)
    checks = []
    ref_v = classify(run_in_sandbox(task_files(task, task.reference_solution), lim, image, runtime))
    ref_ok = ref_v.category == Category.PASSED
    checks.append({"solution": "reference", "expect": "passed", **ref_v.to_dict(), "ok": ref_ok})
    expected = ref_v.tests_total
    all_ok = ref_ok
    for name, code in task.incorrect_solutions.items():
        v = classify(run_in_sandbox(task_files(task, code), lim, image, runtime), expected)
        ok = v.category in (Category.WRONG_ANSWER, Category.RUNTIME_ERROR)
        checks.append({"solution": name, "expect": "fail", **v.to_dict(), "ok": ok})
        all_ok = all_ok and ok
    return ValidationResult(task.id, all_ok, expected, checks)


def evaluate(
    tasks: list[Task],
    model: Model,
    params: GenerationParams,
    samples: int = 1,
    limits: SandboxLimits | None = None,
    image: str = DEFAULT_IMAGE,
    on_sample=None,
    runtime: str = DEFAULT_RUNTIME,
) -> dict:
    """Run a full evaluation and return the run document (a JSON-serializable dict)."""
    limits = limits or SandboxLimits()
    run = {
        "run_id": f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        "started_at": _now(),
        "is_mock": model.is_mock,
        "notice": "MOCK MODEL — synthetic output, NOT a real model result"
        if model.is_mock
        else None,
        "harness": harness_info(),
        "model": {"id": model.model_id, "is_mock": model.is_mock, "params": params.to_dict()},
        "sandbox_backend": os.environ.get("EVALHARNESS_SANDBOX_BACKEND", "docker"),
        "sandbox_runtime": runtime,
        "samples_per_task": samples,
        "validations": [],
        "samples": [],
    }
    for task in tasks:
        val = validate_task(task, limits, image, runtime)
        run["validations"].append(val.to_dict())
        if not val.ok:
            raise TaskValidationError(
                f"task {task.id!r} failed validation; refusing to score it: {val.checks}"
            )
        prompt = build_prompt(task)
        lim = task_limits(task, limits)
        for i in range(samples):
            completion = model.generate(prompt, params)
            code = extract_code(completion.text)
            outcome = run_in_sandbox(task_files(task, code), lim, image, runtime)
            verdict: Verdict = classify(outcome, val.expected_tests)
            record = {
                "task": {"id": task.id, "version": task.version, "content_hash": task.content_hash},
                "sample_index": i,
                "model_id": completion.model_id,
                "is_mock": completion.is_mock,
                "prompt": prompt,
                "completion": completion.text,
                "generated_code": code,
                "generation_latency_s": round(completion.latency_s, 4),
                "result": verdict.to_dict(),
                "sandbox": _outcome_summary(outcome),
                "finished_at": _now(),
            }
            run["samples"].append(record)
            if on_sample:
                on_sample(record)
    run["finished_at"] = _now()
    return run
