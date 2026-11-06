"""Unit tests for the classifier. These use synthetic outcomes and need no Docker."""

import errno

import pytest

from evalharness.sandbox import SandboxOutcome, parse_envelope
from evalharness.scoring import Category, classify

LIMITS = {"timeout_s": 5, "memory_mb": 256}


def exc(type_, mro=None, eno=None, msg="boom"):
    return {
        "type": type_,
        "mro": mro or [type_, "Exception", "BaseException", "object"],
        "errno": eno,
        "message": msg,
    }


def outcome(report=None, rc=0, **kw) -> SandboxOutcome:
    env = {
        "protocol": 1,
        "syntax_ok": True,
        "timed_out": False,
        "returncode": rc,
        "report": report,
        "log_tail": "",
    }
    env.update(kw.pop("env", {}))
    return SandboxOutcome(image="img", envelope=env, limits=LIMITS, **kw)


def report(results, collected=None, **kw):
    r = {
        "collected": len(results) if collected is None else collected,
        "exitstatus": 0,
        "import_error": None,
        "collect_errors": [],
        "results": results,
    }
    r.update(kw)
    return r


def ok(n="t"):
    return {"nodeid": n, "when": "call", "outcome": "passed", "exception": None}


def fail(e, n="t"):
    return {"nodeid": n, "when": "call", "outcome": "failed", "exception": e}


def test_passed():
    v = classify(outcome(report([ok("a"), ok("b")])), expected_tests=2)
    assert (v.category, v.tests_passed, v.tests_total) == (Category.PASSED, 2, 2)


def test_wrong_answer_on_assertion_and_pytest_failed():
    failed = exc("Failed", ["Failed", "OutcomeException", "BaseException", "object"])
    v = classify(outcome(report([ok(), fail(exc("AssertionError")), fail(failed)]), rc=1))
    assert v.category == Category.WRONG_ANSWER
    assert v.tests_passed == 1


def test_runtime_error_beats_wrong_answer():
    v = classify(outcome(report([fail(exc("AssertionError")), fail(exc("KeyError"))]), rc=1))
    assert v.category == Category.RUNTIME_ERROR


@pytest.mark.parametrize(
    "e",
    [
        exc("PermissionError", ["PermissionError", "OSError"], errno.EACCES),
        exc("OSError", ["OSError"], errno.EROFS),
        exc("OSError", ["OSError"], errno.ENETUNREACH),
        exc("gaierror", ["gaierror", "OSError"], -3),
    ],
)
def test_sandbox_violation_from_exceptions(e):
    v = classify(outcome(report([fail(e)]), rc=1))
    assert v.category == Category.SANDBOX_VIOLATION


@pytest.mark.parametrize(
    "e",
    [
        exc("MemoryError"),
        exc("BlockingIOError", ["BlockingIOError", "OSError"], errno.EAGAIN),
        exc("OSError", ["OSError"], errno.ENOSPC),
    ],
)
def test_resource_limit_from_exceptions(e):
    assert classify(outcome(report([fail(e)]), rc=1)).category == Category.RESOURCE_LIMIT


def test_violation_beats_resource_and_runtime():
    r = report([fail(exc("MemoryError")), fail(exc("PermissionError", ["PermissionError"], 13))])
    assert classify(outcome(r, rc=1)).category == Category.SANDBOX_VIOLATION


def test_syntax_error():
    o = outcome(env={"syntax_ok": False, "syntax_detail": "SyntaxError: expected ':'"})
    assert classify(o).category == Category.SYNTAX_ERROR


def test_timeout_inner_and_host():
    assert classify(outcome(env={"timed_out": True})).category == Category.TIMEOUT
    assert classify(outcome(host_timed_out=True)).category == Category.TIMEOUT


def test_oom_killed():
    assert classify(outcome(oom_killed=True)).category == Category.RESOURCE_LIMIT


def test_killed_by_signal_without_report_is_resource_limit():
    assert classify(outcome(None, rc=-9)).category == Category.RESOURCE_LIMIT


def test_clean_exit_without_report_is_violation():
    # e.g. the solution called os._exit(0) to dodge the tests
    assert classify(outcome(None, rc=0)).category == Category.SANDBOX_VIOLATION


def test_missing_envelope_and_output_flood_are_violations():
    o = SandboxOutcome(image="img", envelope=None, envelope_problem="forged", limits=LIMITS)
    assert classify(o).category == Category.SANDBOX_VIOLATION
    assert classify(outcome(output_exceeded=True)).category == Category.SANDBOX_VIOLATION


def test_harness_error_takes_precedence():
    o = outcome(report([ok()]), harness_error="image missing", host_timed_out=True)
    assert classify(o).category == Category.HARNESS_ERROR


def test_test_count_mismatch_is_violation():
    # all collected tests passed, but tests went missing (e.g. the solution deleted a test file)
    v = classify(outcome(report([ok()])), expected_tests=5)
    assert v.category == Category.SANDBOX_VIOLATION
    assert "has 5" in v.detail


def test_zero_tests_is_not_a_pass():
    assert classify(outcome(report([]))).category == Category.SANDBOX_VIOLATION


def test_import_errors():
    r = report([], collected=0, import_error=exc("ZeroDivisionError"))
    assert classify(outcome(r, rc=3)).category == Category.RUNTIME_ERROR
    r = report([], collected=0, import_error=exc("PermissionError", ["PermissionError"], 13))
    assert classify(outcome(r, rc=3)).category == Category.SANDBOX_VIOLATION


def test_collection_error_is_runtime_error():
    r = report([], collected=0, collect_errors=["ImportError: cannot import name 'f'"])
    assert classify(outcome(r, rc=2)).category == Category.RUNTIME_ERROR


NONCE = "a" * 32


def _env_text(body='{"protocol": 1}', nonce=NONCE):
    return (
        f"===EVALHARNESS-ENVELOPE-BEGIN:{nonce}===\n{body}\n"
        f"===EVALHARNESS-ENVELOPE-END:{nonce}===\n"
    )


def test_parse_envelope_ok():
    env, problem = parse_envelope("noise\n" + _env_text(), NONCE)
    assert env == {"protocol": 1} and problem is None


def test_parse_envelope_rejects_forgery_and_wrong_nonce():
    forged = _env_text(nonce="b" * 32)
    assert parse_envelope(forged + _env_text(), NONCE)[0] is None  # extra (forged) marker
    assert parse_envelope(forged, NONCE)[0] is None  # only a wrong-nonce envelope
    assert parse_envelope(_env_text() + _env_text(), NONCE)[0] is None  # duplicated
    assert parse_envelope(_env_text(body="not json"), NONCE)[0] is None


def test_network_errors_count_as_violations_even_when_packets_are_dropped():
    # Kubernetes NetworkPolicy drops packets, so connect() times out instead of failing fast.
    timeout = {
        **exc("TimeoutError", ["TimeoutError", "OSError", "Exception"]),
        "origin": "socket.py",
    }
    refused = {
        **exc("ConnectionRefusedError", ["ConnectionRefusedError", "OSError"]),
        "origin": "ssl.py",
    }
    for e in (timeout, refused):
        assert classify(outcome(report([fail(e)]))).category == Category.SANDBOX_VIOLATION


def test_timeout_error_from_the_solution_itself_is_a_runtime_error():
    own = {**exc("TimeoutError", ["TimeoutError", "OSError", "Exception"]), "origin": "solution.py"}
    assert classify(outcome(report([fail(own)]))).category == Category.RUNTIME_ERROR
