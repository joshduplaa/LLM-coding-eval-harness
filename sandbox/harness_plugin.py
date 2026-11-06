"""pytest plugin baked into the sandbox image. Writes a JSON report of what happened.

For each failing test it records the exception's type, its base-class names, and its errno, so
the host can tell a wrong answer (AssertionError) from a crash, a resource limit (MemoryError,
EAGAIN from fork), or a sandbox boundary hit (PermissionError, network unreachable).

It imports `solution` itself at session start, so an import-time failure is captured with its
real exception type instead of being buried in a collection error.

Limitation: this plugin runs in the same process as the untrusted code, so deliberately adversarial
code can tamper with it. The host adds cross-checks (the expected test count, exactly one tagged
envelope), but in-process testing can't be made fully tamper-proof. See docs/DECISIONS.md.
"""

from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

_results: list[dict] = []
_collect_errors: list[str] = []
_import_error: dict | None = None


def _exc_info(e: BaseException) -> dict:
    return {
        "type": type(e).__name__,
        "mro": [c.__name__ for c in type(e).__mro__],
        "errno": getattr(e, "errno", None) if isinstance(getattr(e, "errno", None), int) else None,
        "message": str(e)[:500],
    }


def _write(session_exitstatus: int | None, collected: int) -> None:
    report = {
        "collected": collected,
        "exitstatus": int(session_exitstatus) if session_exitstatus is not None else None,
        "import_error": _import_error,
        "collect_errors": _collect_errors[:20],
        "results": _results[:500],
    }
    path = os.environ["HARNESS_REPORT"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f)
    os.replace(tmp, path)


def pytest_sessionstart(session: pytest.Session) -> None:
    global _import_error
    work = os.environ.get("HARNESS_WORK", ".")
    if work not in sys.path:
        sys.path.insert(0, work)
    try:
        importlib.import_module("solution")
    except BaseException as e:  # noqa: BLE001 - we want SystemExit/KeyboardInterrupt too
        _import_error = _exc_info(e)
        _write(None, 0)
        pytest.exit("solution failed to import", returncode=3)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    rep = yield
    if rep.when == "call" or rep.outcome != "passed":
        exc = None
        if call.excinfo is not None:
            exc = _exc_info(call.excinfo.value)
        _results.append(
            {"nodeid": item.nodeid, "when": rep.when, "outcome": rep.outcome, "exception": exc}
        )
    return rep


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _collect_errors.append(str(report.longrepr)[-1500:])


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _import_error is None:
        _write(exitstatus, session.testscollected)
