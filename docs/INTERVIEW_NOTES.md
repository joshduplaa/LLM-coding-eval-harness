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

### 9. Adding gVisor, and the limit that meant something different
Containers share the host kernel, so I added gVisor as an opt-in runtime: one `--runtime runsc`
flag, recorded on every result. Running the same 71 tests under both runtimes found two
differences. (1) `Seccomp: 0` in `/proc/self/status`, because gVisor applies the profile internally.
The probe now checks that the sandbox sees a different kernel instead. (2) The fork bomb came back
with no output at all. Under gVisor, `--pids-limit` caps the host threads of gVisor's own kernel,
so the bomb starved gVisor until it crashed. I measured the threshold (crash at 48, fine at 56),
set a floor of 256 under runsc, and let the memory limit stop fork bombs there instead.
**Lessons:** the same flag can mean something different under a different runtime, so the tests
have to run under each runtime you claim to support. Also, the classifier failed *safe*: an
unexplained missing result was called a violation, never a pass.

### 10. Numbers worth knowing
Container start plus pytest run: ~0.6 s per sample. Validation: ~1.5 s per task. A timeout sample
costs the full timeout (10 s by default). That's why timeouts dominate wall time in `mock-mixed` runs.
gVisor: about 1.36 s vs 0.87 s per container, about 0.5 s of overhead per sample.

## Milestone 2

### 11. "How do you report a pass rate honestly?"
Always three things together: the rate, the sample size it came from, and an interval. 4/15 is
26.7%, but the 95% Wilson interval is 10.9%–52.0%, so "about a quarter" is all you can say. I
chose Wilson over the textbook Wald interval because Wald claims [100%, 100%] from 5/5. I also
state the limitation that samples of the same task are correlated, so the overall interval is
optimistic. Infrastructure failures are excluded from the denominator but always shown, because a
hidden exclusion bucket can be gamed.

### 12. "Why store JSON in a SQL database?"
The record schema will keep growing (it already gained runtime fields in Milestone 1), and
reproducibility needs *every* field. So the full record is stored as JSON, and only the fields
we query are copied into indexed columns. It's append-only, with one transaction per run and
parameterized queries throughout.

### 13. Supply chain: the `httpx2` warning
A library warning told me to install a package whose name differs from a popular one by one
character, which is exactly what typosquatting looks like. I checked that the advice came from
Starlette's own source and that the PyPI metadata had the same author and a legitimate repo
before installing. It takes two minutes and is worth doing every time.

## Milestone 3

### 14. "How do you keep secrets out of Docker images and CI?"
Allowlist `.dockerignore`, so the build context only contains what's listed. A smoke test
asserts `.env`, `.git`, and the results directories aren't in the image. The image was also
tested against a deliberately bad copy with a `.env` planted in it, to prove the check fails
when it should. In CI: no secrets referenced at all, a read-only token, and
`persist-credentials: false`. The easiest secret to protect is one the pipeline never has.

### 15. "Why pin actions and images by hash?"
Tags are mutable. The 2025 `tj-actions/changed-files` compromise re-pointed version tags to
code that dumped CI secrets into logs, and anyone pinned to a SHA was unaffected. Same for base
images: our results record the sandbox image ID, so the base needs to be exactly reproducible.
The tradeoff is that patches need a deliberate bump; a bot like Dependabot can automate that.

### 16. "Why is the API bound to 0.0.0.0 in the container if localhost is safer?"
Because inside a container, localhost is the container itself. Docker forwards published ports
to the container's network interface, not its loopback. So the container listens on all of
*its* interfaces, and the real exposure decision is made by `-p 127.0.0.1:8000:8000` on the
host. Same principle, enforced at a different layer.

### 17. "How do you run untrusted code on Kubernetes?"
One disposable pod per sample, in a namespace that enforces the restricted Pod Security
Standard and denies all traffic, with no service-account token, a read-only root, memory and
CPU limits, and a deadline. The evaluator has RBAC for pods in that namespace only. I rejected
mounting the Docker socket, because the socket is effectively root on the node.

### 18. "Tell me about a bug your tests caught."
The adversarial suite ran unchanged against the new Kubernetes backend, and one test failed: a
pod's very first connection to the internet succeeded even though a deny-all NetworkPolicy
existed. The network plugin needs a moment to learn about a new pod, and until then its traffic
is allowed. The fix had to be fail-closed and had to run before any untrusted code. The trusted
wrapper now proves the block is in force, by checking that a normally reachable in-cluster
address is dropped, before running the solution, and it records a harness error if the block
never appears. Lessons: run the same attack tests against every backend, and treat "the policy
exists" and "the policy is enforced" as different claims.

### 19. "Why does your Grafana have no login?"
A login means a credential to create, store, rotate, and possibly leak. The dashboard is
read-only and reached only by port-forward to localhost, so anonymous Viewer with no login
form, no basic auth, and no admin user removes the credential entirely. The dashboard is
provisioned from code, so nobody needs to edit it in the UI.
