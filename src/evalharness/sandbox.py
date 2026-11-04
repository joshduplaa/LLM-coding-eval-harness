"""Host side of the sandbox: start a hardened, throwaway container and collect its result.

Each isolation property comes from a specific `docker run` flag (see `docker_run_args`):

    no network               --network none            (only a loopback interface exists)
    read-only filesystem     --read-only + a small noexec tmpfs at /tmp for scratch space
    no host filesystem       no -v / bind mounts at all; files go in over stdin
    no secrets               host env vars are never passed (`docker run` doesn't inherit them)
    non-root                 --user 10001:10001
    no privilege escalation  --cap-drop ALL, --security-opt no-new-privileges
    CPU / memory / procs     --cpus, --memory (with swap disabled), --pids-limit, --ulimit nofile
    time                     inner deadline in the wrapper, plus a host deadline and `docker kill`
    output size              the host stops reading after MAX_OUTPUT bytes and kills the container

Docker's default seccomp and AppArmor profiles stay on; we never pass --privileged.
"""

from __future__ import annotations

import json
import os
import secrets
import selectors
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field

from .errors import HarnessError

DEFAULT_IMAGE = os.environ.get("EVALHARNESS_SANDBOX_IMAGE", "evalharness-sandbox:0.1.0")
MAX_OUTPUT = 1024 * 1024
STARTUP_GRACE_S = 15.0
SANDBOX_UID = "10001:10001"
ENVELOPE_PREFIX = "===EVALHARNESS-ENVELOPE-BEGIN"


@dataclass(frozen=True)
class SandboxLimits:
    timeout_s: float = 10.0
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64
    tmpfs_mb: int = 32
    nofile: int = 256

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SandboxOutcome:
    """Raw facts about one sandbox execution, before any classification."""

    image: str
    image_id: str | None = None
    docker_returncode: int | None = None
    host_timed_out: bool = False
    output_exceeded: bool = False
    oom_killed: bool = False
    envelope: dict | None = None
    envelope_problem: str | None = None  # why no trustworthy envelope was found
    stderr_tail: str = ""
    duration_s: float = 0.0
    harness_error: str | None = None
    limits: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def docker_run_args(name: str, image: str, limits: SandboxLimits) -> list[str]:
    tmp_opts = "rw,noexec,nosuid,nodev"
    flags: list[tuple[str, str | None]] = [
        ("--interactive", None),  # files arrive on stdin
        ("--name", name),
        ("--label", "evalharness=sandbox"),
        ("--network", "none"),
        ("--read-only", None),
        ("--tmpfs", f"/tmp:{tmp_opts},size={limits.tmpfs_mb}m"),  # noqa: S108 - in-container path
        # Separate mount for the result report, so filling /tmp can't block the report.
        ("--tmpfs", f"/harness-out:{tmp_opts},size=1m"),
        ("--user", SANDBOX_UID),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--ipc", "none"),
        ("--memory", f"{limits.memory_mb}m"),
        ("--memory-swap", f"{limits.memory_mb}m"),  # equal to --memory means no swap
        ("--cpus", str(limits.cpus)),
        ("--pids-limit", str(limits.pids)),
        ("--ulimit", f"nofile={limits.nofile}:{limits.nofile}"),
        ("--hostname", "sandbox"),
    ]
    args = ["docker", "run"]
    for flag, value in flags:
        args += [flag] if value is None else [flag, value]
    return [*args, image]


def _docker(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def image_id(image: str) -> str:
    """Return the image's content ID (a sha256), or raise HarnessError if it can't be used."""
    try:
        r = _docker("image", "inspect", "--format", "{{.Id}}", image)
    except FileNotFoundError as e:
        raise HarnessError("docker CLI not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise HarnessError("docker did not respond (is the daemon running?)") from e
    if r.returncode != 0:
        msg = r.stderr.strip()
        if "Cannot connect" in msg or "permission denied" in msg.lower():
            raise HarnessError(f"cannot reach the Docker daemon: {msg}")
        raise HarnessError(
            f"sandbox image {image!r} not found. Build it with `make sandbox-image`. ({msg})"
        )
    return r.stdout.strip()


def _bounded_communicate(
    proc: subprocess.Popen, data: bytes, deadline: float
) -> tuple[bytes, bytes, bool, bool]:
    """Send `data`, then read stdout and stderr until EOF, the deadline, or MAX_OUTPUT bytes.

    Returns (stdout, stderr, timed_out, exceeded). We can't use subprocess.run(capture_output=...)
    here: it buffers everything, so code that prints in a loop could exhaust host memory.
    """
    if not (proc.stdin and proc.stdout and proc.stderr):
        raise HarnessError("sandbox process has no pipes")
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "out")
    sel.register(proc.stderr, selectors.EVENT_READ, "err")
    bufs = {"out": bytearray(), "err": bytearray()}
    timed_out = exceeded = False
    while sel.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in sel.select(timeout=min(remaining, 0.5)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                sel.unregister(key.fileobj)
                continue
            bufs[key.data] += chunk
            if len(bufs["out"]) + len(bufs["err"]) > MAX_OUTPUT:
                exceeded = True
                break
        if exceeded:
            break
    sel.close()
    return bytes(bufs["out"]), bytes(bufs["err"]), timed_out, exceeded


def parse_envelope(stdout: str, nonce: str) -> tuple[dict | None, str | None]:
    """Find the single envelope tagged with `nonce`. Returns (envelope, problem)."""
    total_markers = stdout.count(ENVELOPE_PREFIX)
    begin = f"===EVALHARNESS-ENVELOPE-BEGIN:{nonce}==="
    end = f"===EVALHARNESS-ENVELOPE-END:{nonce}==="
    if stdout.count(begin) != 1 or stdout.count(end) != 1:
        return None, "no correctly tagged result envelope (the process was killed or tampered with)"
    if total_markers != 1:
        return None, f"found {total_markers} envelope markers; expected exactly 1 (forgery attempt)"
    body = stdout.split(begin, 1)[1].split(end, 1)[0]
    try:
        env = json.loads(body)
    except ValueError:
        return None, "result envelope is not valid JSON"
    if not isinstance(env, dict) or env.get("protocol") != 1:
        return None, "result envelope has an unexpected format"
    return env, None


def run_in_sandbox(
    files: dict[str, str],
    limits: SandboxLimits | None = None,
    image: str = DEFAULT_IMAGE,
) -> SandboxOutcome:
    """Run pytest over `files` (which must include solution.py) in a fresh container."""
    limits = limits or SandboxLimits()
    out = SandboxOutcome(image=image, limits=limits.to_dict())
    try:
        out.image_id = image_id(image)
    except HarnessError as e:
        out.harness_error = str(e)
        return out

    name = f"evalh-{uuid.uuid4().hex[:12]}"
    nonce = secrets.token_hex(16)
    payload = json.dumps({"files": files, "timeout_s": limits.timeout_s, "nonce": nonce}).encode()
    start = time.monotonic()
    proc = subprocess.Popen(
        docker_run_args(name, image, limits),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ},  # the docker *CLI* needs DOCKER_HOST/CONTEXT; the container gets none
    )
    try:
        stdout, stderr, timed_out, exceeded = _bounded_communicate(
            proc, payload, start + limits.timeout_s + STARTUP_GRACE_S
        )
        if timed_out or exceeded:
            # Kill our `docker run` client first. If we stop reading but leave it attached, the
            # daemon blocks writing the container's output and `docker kill` can hang. Then kill
            # the container itself. (`docker rm --force` in `finally` is the last resort.)
            proc.kill()
            proc.wait()
            try:
                _docker("kill", name, timeout=30)
            except subprocess.TimeoutExpired:
                pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        out.duration_s = round(time.monotonic() - start, 3)
        out.docker_returncode = proc.returncode
        out.host_timed_out = timed_out
        out.output_exceeded = exceeded
        out.stderr_tail = stderr.decode("utf-8", "replace")[-4000:]

        state = _docker("inspect", "--format", "{{json .State}}", name)
        if state.returncode == 0:
            st = json.loads(state.stdout)
            out.oom_killed = bool(st.get("OOMKilled"))
        elif not (timed_out or exceeded):
            out.harness_error = f"container did not start: {out.stderr_tail.strip()[-500:]}"
            return out

        out.envelope, out.envelope_problem = parse_envelope(
            stdout.decode("utf-8", "replace"), nonce
        )
        # 125/126/127 mean docker itself couldn't run the container. The code under test can't
        # cause these: it can't signal PID 1 (the wrapper), and the wrapper always exits 0/2.
        if out.envelope is None and proc.returncode in (125, 126, 127) and not timed_out:
            out.harness_error = f"docker run failed ({proc.returncode}): {out.stderr_tail[-500:]}"
        elif out.envelope and out.envelope.get("wrapper_error"):
            out.harness_error = f"sandbox wrapper error: {out.envelope['wrapper_error']}"
        return out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _docker("rm", "--force", name, timeout=30)
