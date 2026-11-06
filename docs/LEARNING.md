# Learning notes

Concepts in the order the project introduces them. Each one says what it is, why it matters
here, and where it lives in the code.

## Milestone 1

### Containers are processes with restricted views, not VMs
A container is an ordinary Linux process that the kernel gives:
- **Namespaces**, which restrict what it can *see*: its own filesystem tree (mount namespace),
  process list (PID namespace, where it's PID 1), network stack (network namespace), and hostname.
- **cgroups**, which restrict how much it can *use*: memory, CPU time, number of processes.

There is no second kernel, which is why startup takes milliseconds, and also why a kernel bug
can break isolation. Stronger options put a second kernel boundary in between (gVisor intercepts
syscalls in user space; Firecracker runs a micro-VM).

### Mapping each requirement to a mechanism (`src/evalharness/sandbox.py`)
| Requirement | Mechanism | How it shows up when violated |
|---|---|---|
| No network | `--network none`: the namespace has only `lo` | `OSError: [Errno 101] Network is unreachable`, DNS `gaierror` |
| Read-only FS | `--read-only` rootfs plus a small tmpfs at `/tmp` | `OSError: [Errno 30] Read-only file system` or `PermissionError` |
| No host files | no `-v`/`--mount` at all; files arrive on stdin | the paths simply don't exist |
| No secrets | `docker run` doesn't inherit the host's env; we pass no `-e` | the variables aren't there |
| Non-root | `--user 10001:10001` | `PermissionError` on privileged operations |
| No escalation | `--cap-drop ALL` plus `no-new-privileges` | `os.setuid(0)` gives `PermissionError`; setuid binaries don't elevate |
| Memory | `--memory 256m --memory-swap 256m` (cgroup) | the kernel OOM-killer sends SIGKILL; Docker sets `OOMKilled` |
| Processes | `--pids-limit 64` (cgroup) | `fork()` fails with `EAGAIN` (`BlockingIOError`) |
| CPU | `--cpus 1` (CFS quota) | the code just runs slower; it can't starve the host |
| Time | the wrapper's deadline kills the process group; the host deadline runs `docker kill` | `timeout` |

**Linux capabilities** split root's powers into ~40 pieces (e.g. `CAP_NET_RAW`, `CAP_SYS_ADMIN`).
Even a non-root process can hold some. `--cap-drop ALL` removes all of them, and the probe test
checks that `CapEff` is 0.

**seccomp** is a kernel filter on system calls. Docker's default profile blocks ~50 dangerous
ones (e.g. `mount`, `kexec_load`). The probe checks that `Seccomp: 2` (filter mode) is active.

### Why PID 1 is special
Inside a PID namespace, the first process is "init". The kernel won't deliver a signal to init
from inside its namespace unless init has installed a handler for it, and that includes SIGKILL.
Our wrapper runs as PID 1, so the untrusted child can't kill it. When PID 1 exits, the kernel
kills everything else in the namespace, which cleans up fork bombs for free.

### Process groups and sessions
`start_new_session=True` makes the child the leader of a new session and process group. Its
forks inherit that group, so `os.killpg(pgid, SIGKILL)` kills the whole tree at once. Killing only
the child PID would leave grandchildren running.

### Why a bounded reader instead of `subprocess.run(capture_output=True)`
`capture_output` buffers *everything* in memory. Code that prints in a loop could fill host
RAM within the timeout. `_bounded_communicate` reads with `selectors` (non-blocking
multiplexing over stdout and stderr) and stops at 1 MiB. The flood test caught a real-world
subtlety: if you stop reading but leave the `docker run` client attached, the daemon blocks
writing output, and even `docker kill` can hang. So: kill the client, then the container.

### Trusting the result channel: nonces
Anything the untrusted code can write to, it can forge. A **nonce** ("number used once") is a
random value the host generates per run and shares only with the trusted wrapper over stdin,
where the untrusted child can't read it. A forged result can't carry the right nonce. We also
require *exactly one* marker of any kind, so a forgery attempt is detected even when the real
envelope is also present.

### Evaluation integrity concepts
- **Test-suite validation:** a test suite proves nothing unless it *rejects* a wrong answer.
  Running a known-incorrect solution checks the suite's discriminating power. It's a tiny
  mutation-testing idea.
- **Reproducibility record:** with the same task content hash, prompt, model id plus params, sandbox
  image ID, and harness commit, someone else can rerun and compare. The image *ID* (a content hash)
  pins the exact environment better than a tag, which can move.
- **Failure taxonomy:** collapsing every failure into "fail" hides harness bugs and sandbox events.
  A `harness_error` must never count against a model.
- **Precedence:** when several things go wrong, one rule decides the category. The rule puts trust
  and infrastructure first (harness, timeout, OOM, forged output), then the code's behavior.

### OCI runtimes, runc, and gVisor
Docker doesn't create containers itself. It hands an OCI bundle (a filesystem plus a JSON config
of namespaces, cgroups, mounts, and seccomp) to a **runtime** binary. **runc** is the default.
It asks the host kernel to set up the namespaces and cgroups, then `exec`s your process, which
then makes syscalls directly to the host kernel. **runsc** (gVisor) starts a **Sentry** instead:
a Go program that implements the Linux syscall interface in user space. Your process's syscalls
are intercepted (via ptrace or, by default now, the "systrap" platform) and served by the
Sentry. Only the Sentry talks to the host kernel, through a short allow-list of syscalls.

Things that change under gVisor, and why:
- `/proc/version` shows gVisor's fake kernel, not the host's. That's the easiest way to tell
  you're inside.
- Host-level cgroup limits apply to the *Sentry process*. Memory still works as expected (the
  Sentry's memory is the sandbox's memory). The pids limit counts the Sentry's host threads,
  which is why it needs headroom (D13).
- Syscalls cost more (interception), so startup and I/O-heavy code are slower. Pure computation
  runs at almost full speed.

### pytest internals used here
- **Plugins and hooks:** pytest calls named functions (`pytest_sessionstart`,
  `pytest_runtest_makereport`, ...) on any module loaded with `-p name`. A `wrapper=True` hook
  wraps pytest's own implementation; `rep = yield` gets the result.
- `pytest.raises` failing raises pytest's own `Failed` exception, not `AssertionError`. Both are
  "the test's expectation was not met", so both count as `wrong_answer`.
- Exit codes: 0 all passed, 1 some failed, 2 interrupted, 3 internal error, 4 usage, 5 no tests.

## Milestone 2

### Confidence intervals, in plain terms
A pass rate from 15 samples is an estimate. If you reran with 15 fresh samples, you'd get a
somewhat different number. A **95% confidence interval** is a range built by a method that,
across many repetitions, contains the true rate 95% of the time. Width shrinks roughly as
`1/sqrt(n)`: to halve the width you need about four times the samples. Two runs whose intervals
overlap heavily have *not* been shown to differ. That's one reason the project never says one
model is better.

**Wald vs Wilson.** Wald, `p ± 1.96·sqrt(p(1-p)/n)`, is centered on the observed rate. At 5/5 the
spread term is 0, so it claims [100%, 100%]. Wilson asks instead: "which true rates p would make
this observation unsurprising?" It solves `|p̂ − p| ≤ 1.96·sqrt(p(1−p)/n)` for p. The width uses
the *candidate* p, not the observed one, so it never collapses to zero and stays inside [0, 1].
`tests/test_metrics.py` checks our formula against that quadratic solved directly.

**Independence.** The math assumes each sample is an independent coin flip. Ten samples of
one hard task aren't ten independent flips. They're closer to one flip repeated, so the real
uncertainty is wider than the printed interval. The report says so.

### SQLite essentials used here
- A single file, no server. Python ships with it (`sqlite3`).
- **Transactions:** `with connection:` commits if the block succeeds and rolls back if it
  raises. Saving a run is one transaction, so a crash halfway leaves *nothing*, never half a
  run (tested in `test_failed_save_writes_nothing`).
- **Parameterized queries:** `execute("... WHERE run_id = ?", (run_id,))`. The database receives
  the value separately from the SQL text, so it can never be run as SQL. This is the defense
  against SQL injection. Never build SQL with f-strings from data.
- **`PRAGMA user_version`:** a free integer in the file header, used here as the schema version.

### HTTP APIs with FastAPI
- An endpoint is a function with a decorator: `@app.get("/runs/{run_id}")`. Path parameters come
  from the URL, and query parameters (`?category=timeout`) from the function's arguments.
- Type hints are validation. `category: Category | None` rejects unknown values with **422
  Unprocessable Entity** before our code runs. `Query(100, ge=1, le=1000)` bounds `limit`.
- Status codes used: 200 OK, 404 Not Found (no such run), 405 Method Not Allowed (POST/DELETE on
  a read-only API), 422 (bad input).
- `create_app(db_path)` is an **app factory**: tests build an app over a temporary database and
  call it in-process with `TestClient`, with no real network.
- Binding to `127.0.0.1` means only this machine can connect; `0.0.0.0` would expose it to the
  network. Default to the narrow one.
