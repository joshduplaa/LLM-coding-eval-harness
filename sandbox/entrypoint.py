"""Trusted wrapper that runs as PID 1 inside the sandbox container.

The host sends one JSON document on stdin:
    {"files": {"solution.py": "...", "test_x.py": "..."}, "timeout_s": 10, "nonce": "<hex>"}

The wrapper:
  1. writes the files to /tmp/work (a size-limited tmpfs; the root filesystem is read-only),
  2. checks that solution.py compiles, in a child process (compiling does not execute code,
     but a pathological file can still crash or hang the parser, so it gets its own process),
  3. runs pytest with the harness plugin in a child process, in its own session, so the whole
     process group can be killed when the time budget runs out,
  4. prints exactly one result "envelope" on stdout between markers that contain the nonce.

Untrusted code only ever runs in the child processes. It can't get at the nonce, because the nonce
travels on stdin and is never written to disk or put in the environment. So a forged envelope is
detectable: the host accepts exactly one correctly tagged envelope.

This file uses only the standard library and must stay small and auditable.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

PROTOCOL = 1
WORK = "/tmp/work"
OUT = "/tmp/out"
# The report lives on its own small tmpfs, so code that fills /tmp (by accident or not)
# can't prevent the report from being written.
REPORT = "/harness-out/report.json"
LOG = os.path.join(OUT, "log.txt")
MAX_STDIN = 2 * 1024 * 1024
MAX_LOG_TAIL = 16 * 1024
MAX_REPORT = 256 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")
MARK_BEGIN = "===EVALHARNESS-ENVELOPE-BEGIN:{nonce}==="
MARK_END = "===EVALHARNESS-ENVELOPE-END:{nonce}==="

SYNTAX_CHECK = """
import sys
src = open(sys.argv[1], "rb").read()
try:
    compile(src, "solution.py", "exec", dont_inherit=True)
except BaseException as e:  # SyntaxError, ValueError (null bytes), RecursionError, MemoryError
    print(f"{type(e).__name__}: {e}")
    sys.exit(1)
"""


def _emit(envelope: dict, nonce: str) -> None:
    envelope["protocol"] = PROTOCOL
    sys.stdout.write("\n" + MARK_BEGIN.format(nonce=nonce) + "\n")
    sys.stdout.write(json.dumps(envelope))
    sys.stdout.write("\n" + MARK_END.format(nonce=nonce) + "\n")
    sys.stdout.flush()


def _run_child(argv: list[str], deadline: float, env: dict[str, str]) -> tuple[int | None, bool]:
    """Run a child in its own session with output going to LOG. Returns (returncode, timed_out)."""
    with open(LOG, "ab") as log:
        proc = subprocess.Popen(
            argv,
            cwd=WORK,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            timed_out = False
        except subprocess.TimeoutExpired:
            rc, timed_out = None, True
        finally:
            # Kill the whole process group, including anything the code forked.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
    return rc, timed_out


def _tail(path: str, limit: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_report() -> dict | None:
    try:
        if os.path.getsize(REPORT) > MAX_REPORT:
            return {"invalid": "report too large"}
        with open(REPORT, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return {"invalid": f"unreadable report: {type(e).__name__}"}


def main() -> None:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    try:
        spec = json.loads(raw)
        nonce = str(spec["nonce"])
        if not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise ValueError("bad nonce")
    except (ValueError, KeyError, TypeError) as e:
        # No trustworthy nonce: print an untagged error the host will treat as a harness error.
        print(f"EVALHARNESS-WRAPPER-ERROR: bad input: {type(e).__name__}", flush=True)
        sys.exit(2)

    envelope: dict = {
        "wrapper_error": None,
        "syntax_ok": None,
        "syntax_detail": None,
        "timed_out": False,
        "returncode": None,
        "report": None,
        "log_tail": "",
        "elapsed_s": None,
    }
    try:
        if len(raw) > MAX_STDIN:
            raise ValueError("input too large")
        files = spec["files"]
        timeout_s = float(spec["timeout_s"])
        if "solution.py" not in files:
            raise ValueError("solution.py missing")
        os.makedirs(WORK, exist_ok=True)
        os.makedirs(OUT, exist_ok=True)
        for name, content in files.items():
            if not SAFE_NAME.match(name):
                raise ValueError(f"unsafe file name: {name!r}")
            with open(os.path.join(WORK, name), "w", encoding="utf-8") as f:
                f.write(content)
    except (ValueError, KeyError, TypeError, OSError) as e:
        envelope["wrapper_error"] = f"{type(e).__name__}: {e}"
        _emit(envelope, nonce)
        return

    start = time.monotonic()
    deadline = start + timeout_s
    child_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "/opt/harness",
        "HARNESS_REPORT": REPORT,
        "HARNESS_WORK": WORK,
        "LANG": "C.UTF-8",
    }

    # 1. Syntax check.
    rc, timed_out = _run_child(
        [sys.executable, "-I", "-c", SYNTAX_CHECK, os.path.join(WORK, "solution.py")],
        deadline,
        child_env,
    )
    if timed_out:
        envelope["timed_out"] = True
    elif rc != 0:
        envelope["syntax_ok"] = False
        envelope["syntax_detail"] = _tail(LOG, 2000).strip()
        envelope["returncode"] = rc
    else:
        envelope["syntax_ok"] = True
        # Clear the log so the test phase starts fresh.
        open(LOG, "wb").close()
        # 2. Tests.
        rc, timed_out = _run_child(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "harness_plugin",
                "-p",
                "no:cacheprovider",
                "--rootdir",
                WORK,
                WORK,
            ],
            deadline,
            child_env,
        )
        envelope["timed_out"] = timed_out
        envelope["returncode"] = rc
        envelope["report"] = _read_report()

    envelope["elapsed_s"] = round(time.monotonic() - start, 3)
    envelope["log_tail"] = _tail(LOG, MAX_LOG_TAIL)
    _emit(envelope, nonce)


if __name__ == "__main__":
    main()
