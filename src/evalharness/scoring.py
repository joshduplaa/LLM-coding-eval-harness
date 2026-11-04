"""Turn a raw SandboxOutcome into exactly one result category.

This is a pure function with no Docker and no I/O, so every branch gets a plain unit test.

Precedence (first match wins). Earlier checks are about trust and infrastructure, later ones
about the code's behavior:

    harness_error      the harness or Docker failed; says nothing about the model
    timeout            the time budget ran out (inner wrapper deadline or host deadline)
    resource_limit     killed by the memory limit (OOM)
    sandbox_violation  output flood, missing or forged result envelope, or a test-count mismatch
    syntax_error       solution.py doesn't compile
    resource_limit     the report shows MemoryError, fork refused (EAGAIN), disk/tmpfs full, ...
    sandbox_violation  the report shows PermissionError, read-only FS, no network, DNS failure
    runtime_error      some other exception (or import failure / collection error)
    wrong_answer       every failure is a failed test expectation (assert / pytest.raises)
    passed             all expected tests ran and passed
"""

from __future__ import annotations

import errno
from dataclasses import asdict, dataclass
from enum import StrEnum

from .sandbox import SandboxOutcome


class Category(StrEnum):
    PASSED = "passed"
    WRONG_ANSWER = "wrong_answer"
    RUNTIME_ERROR = "runtime_error"
    SYNTAX_ERROR = "syntax_error"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    SANDBOX_VIOLATION = "sandbox_violation"
    HARNESS_ERROR = "harness_error"


RESOURCE_TYPES = {"MemoryError"}
RESOURCE_ERRNOS = {
    errno.EAGAIN,
    errno.ENOSPC,
    errno.EMFILE,
    errno.ENFILE,
    errno.ENOMEM,
    errno.EDQUOT,
}
VIOLATION_TYPES = {"PermissionError", "gaierror", "herror"}
VIOLATION_ERRNOS = {errno.EROFS, errno.EACCES, errno.EPERM, errno.ENETUNREACH, errno.EHOSTUNREACH}
# A failed test expectation: a plain assert, or pytest's own failure exception (raised by
# pytest.raises when nothing was raised, or by pytest.fail). OutcomeException is its base class.
ASSERTION_TYPES = {"AssertionError", "Failed", "OutcomeException"}


@dataclass(frozen=True)
class Verdict:
    category: Category
    detail: str
    tests_passed: int = 0
    tests_total: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = str(self.category)
        return d


def _exc_kind(exc: dict | None) -> str | None:
    """Return 'resource', 'violation', 'assertion', or 'other' for one recorded exception."""
    if not exc:
        return None
    mro = set(exc.get("mro") or [exc.get("type")])
    eno = exc.get("errno")
    if mro & VIOLATION_TYPES or eno in VIOLATION_ERRNOS:
        return "violation"
    if mro & RESOURCE_TYPES or eno in RESOURCE_ERRNOS:
        return "resource"
    if mro & ASSERTION_TYPES:
        return "assertion"
    return "other"


def classify(o: SandboxOutcome, expected_tests: int | None = None) -> Verdict:
    C = Category
    if o.harness_error:
        return Verdict(C.HARNESS_ERROR, o.harness_error)
    env = o.envelope
    if o.host_timed_out or (env and env.get("timed_out")):
        return Verdict(C.TIMEOUT, f"exceeded time limit of {o.limits.get('timeout_s')}s")
    if o.oom_killed:
        return Verdict(C.RESOURCE_LIMIT, f"killed by memory limit ({o.limits.get('memory_mb')} MB)")
    if o.output_exceeded:
        return Verdict(C.SANDBOX_VIOLATION, "output exceeded the host read limit")
    if env is None:
        return Verdict(C.SANDBOX_VIOLATION, o.envelope_problem or "no result envelope")
    if env.get("syntax_ok") is False:
        return Verdict(C.SYNTAX_ERROR, (env.get("syntax_detail") or "")[:500])

    report = env.get("report")
    rc = env.get("returncode")
    if report is None:
        if rc is not None and rc < 0:
            return Verdict(C.RESOURCE_LIMIT, f"test process killed by signal {-rc}")
        return Verdict(C.SANDBOX_VIOLATION, f"test process exited (rc={rc}) without a report")
    if "invalid" in report:
        return Verdict(C.SANDBOX_VIOLATION, f"invalid report: {report['invalid']}")

    if report.get("import_error"):
        exc = report["import_error"]
        kind = _exc_kind(exc)
        msg = f"solution failed to import: {exc.get('type')}: {exc.get('message', '')[:300]}"
        if kind == "violation":
            return Verdict(C.SANDBOX_VIOLATION, msg)
        if kind == "resource":
            return Verdict(C.RESOURCE_LIMIT, msg)
        return Verdict(C.RUNTIME_ERROR, msg)

    results = report.get("results") or []
    total = int(report.get("collected") or 0)
    passed = sum(1 for r in results if r.get("when") == "call" and r.get("outcome") == "passed")
    failures = [r for r in results if r.get("outcome") != "passed"]

    if report.get("collect_errors"):
        return Verdict(
            C.RUNTIME_ERROR,
            "test collection failed: " + report["collect_errors"][0][-300:],
            passed,
            total,
        )

    kinds = {_exc_kind(r.get("exception")) or "other" for r in failures}
    if "violation" in kinds:
        return Verdict(C.SANDBOX_VIOLATION, _first_msg(failures, "violation"), passed, total)
    if "resource" in kinds:
        return Verdict(C.RESOURCE_LIMIT, _first_msg(failures, "resource"), passed, total)
    if "other" in kinds:
        return Verdict(C.RUNTIME_ERROR, _first_msg(failures, "other"), passed, total)
    if failures:
        return Verdict(C.WRONG_ANSWER, f"{len(failures)} of {total} tests failed", passed, total)

    if expected_tests is not None and total != expected_tests:
        return Verdict(
            C.SANDBOX_VIOLATION,
            f"ran {total} tests but the task has {expected_tests}",
            passed,
            total,
        )
    if total == 0 or passed != total or rc != 0:
        return Verdict(
            C.SANDBOX_VIOLATION,
            f"inconsistent report: {passed}/{total} passed, rc={rc}",
            passed,
            total,
        )
    return Verdict(C.PASSED, f"{passed}/{total} tests passed", passed, total)


def _first_msg(failures: list[dict], kind: str) -> str:
    for r in failures:
        exc = r.get("exception")
        if (_exc_kind(exc) or "other") == kind:
            if exc:
                return f"{r.get('nodeid')}: {exc.get('type')}: {exc.get('message', '')[:300]}"
            return f"{r.get('nodeid')}: {r.get('outcome')} during {r.get('when')}"
    return ""
