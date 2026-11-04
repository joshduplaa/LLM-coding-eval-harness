# Interview notes

Problems hit while building, how they were solved, and the tradeoffs worth being able to discuss.

## Milestone 1

### 1. "How do you run untrusted code safely?"
Fresh container per sample, no network, read-only root, no mounts, non-root, all capabilities
dropped, no-new-privileges, cgroup limits on memory/CPU/PIDs, two-level timeouts. Each claim is
backed by a test: a probe runs inside the sandbox and reports uid, CapEff, NoNewPrivs, seccomp
mode, network interfaces, environment, and whether host paths are visible. Hostile solutions then
try network access, DNS, writing to the root filesystem, setuid, fork bombs, memory bombs, filling
the disk, output floods, forging the result, deleting the tests, and exiting early.
**Tradeoff:** containers share the kernel. The next step is gVisor (`--runtime=runsc`), at the cost
of some syscall overhead and an extra install.

### 2. Bug: an incorrect solution classified as `runtime_error` instead of `wrong_answer`
`parse_duration`'s wrong solution doesn't raise on invalid input, so `pytest.raises` failed,
which raises pytest's internal `Failed` exception, not `AssertionError`. Fix: treat
`Failed` and `OutcomeException` as failed expectations. **Lesson:** task validation earns its
keep. It caught a classifier bug before any real model was scored.

### 3. Bug: `docker kill` hung during the output-flood test
Hostile code wrote endlessly to the wrapper's stdout via `/proc/1/fd/1`. The host stopped
reading at 1 MiB, but the `docker run` client was still attached, so the daemon blocked on the
full pipe and `docker kill` timed out after 15 s. Fix: kill the client first, then the container,
with `docker rm --force` in `finally` as a backstop. **Lesson:** backpressure. A writer blocked on a
full pipe can stall things you didn't expect to be connected.

### 4. Bug: filling `/tmp` was misreported as a sandbox violation
The solution filled the 32 MB tmpfs, so the pytest plugin couldn't write its report (Python exited
with status 120, its "couldn't flush at exit" code). With no report, the classifier said
`sandbox_violation`. Fix: put the report on its own 1 MB tmpfs. Now an accidental fill is a clean
`resource_limit` (ENOSPC). Deliberately filling the report mount is still possible and is still
called a violation, which is correct, since it means tampering.

### 5. "Couldn't the model's code just fake passing?"
Partially, and being honest about it matters. The tests run in the same process as the solution,
so hostile code could monkeypatch pytest. Defenses: nonce-tagged single envelope (stops forged
stdout), expected test count from the reference run (stops deleting tests), importing the solution
before test collection, and an exit-code consistency check. Full closure means running tests in a
separate process that talks to the solution over a pipe. That changes the task format, so I
documented it instead of over-building.

### 6. "Why not just use exit codes?"
`os._exit(0)` gives exit code 0 with zero tests run. We require a report with every expected test
passed *and* exit code 0, so this case is recorded as `sandbox_violation`.

### 7. Design: the classifier is a pure function
`classify(outcome, expected_tests) -> Verdict` does no I/O, so all ~25 branches are unit-tested in
milliseconds without Docker. The Docker tests then only need to confirm that real behavior produces
the raw facts those unit tests assume.

### 8. Design: the mock model sees only the prompt
It looks tasks up by exact prompt text, so its interface is exactly what a real API adapter
will have. It's also always labeled: model id `mock/...`, `is_mock: true`, a banner in the CLI.

### 9. Numbers worth knowing
Container start plus pytest run: ~0.6 s per sample. Validation: ~1.5 s per task. A timeout sample
costs the full timeout (10 s by default). That's why timeouts dominate wall time in `mock-mixed` runs.
