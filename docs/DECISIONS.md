# Decisions

Each entry gives the decision, the alternatives considered, and why. Newest entries are at the
bottom of each milestone.

## Milestone 1 — Core harness

### D1. Run untrusted code in a fresh Docker container per sample
- **Chosen:** one throwaway container per sample, started by the harness with `docker run`.
- **Alternatives:** (a) a Python `subprocess` with `resource.setrlimit` on the host: no filesystem
  or network isolation, which breaks the "never run it on my machine" rule. (b) gVisor or
  Firecracker microVMs: stronger isolation, but extra system installs and more complexity than a
  first version needs. (c) A long-lived container reused across samples: faster, but one sample
  could leave state behind for the next.
- **Why:** containers give namespace isolation (filesystem, network, PIDs) plus cgroup resource
  limits with tools already on the machine. Cost: ~0.3–0.7 s of startup per sample.
- **Known gap:** containers share the host kernel, so a kernel exploit could escape. gVisor
  (`--runtime=runsc`) would be the next step up and needs no code changes beyond one flag.

### D2. Send files over stdin, not through a bind mount
- **Chosen:** the host sends a JSON document with the files on the container's stdin, and the trusted
  wrapper writes them to a tmpfs.
- **Alternatives:** `-v hostdir:/work:ro` (simplest, but it exposes part of the host filesystem and
  leaves temporary files on the host); `docker cp` (doesn't work with a read-only root filesystem).
- **Why:** with zero mounts, the container can't see the host filesystem, and the test that checks
  this is simple.

### D3. Hardening flags (all in `sandbox.docker_run_args`)
`--network none`, `--read-only`, a noexec tmpfs at `/tmp` (32 MB), a separate 1 MB tmpfs for the
report, `--user 10001`, `--cap-drop ALL`, `no-new-privileges`, `--ipc none`, `--memory 256m` with
swap disabled, `--cpus 1`, `--pids-limit 64`, `nofile=256`. Docker's default seccomp and AppArmor
profiles stay on. No `-e`, no `-v`, no `--privileged`. A unit test asserts every flag is present.

### D4. A trusted wrapper as PID 1, with untrusted code in a child process group
- **Why:** the wrapper enforces the inner deadline and kills the whole process group, catching
  forked children too. It always produces a structured result. As PID 1 it ignores signals it
  has no handler for, so the untrusted code can't kill it with `kill -9 1`.
- **Two deadlines:** the inner one (wrapper) gives a clean `timeout`. The outer one (host, inner
  plus 15 s of startup grace) runs `docker kill` in case the wrapper itself hangs.

### D5. A result envelope tagged with a per-run nonce; accept exactly one
- **Problem:** the untrusted code runs as the same user as the wrapper, so it can write to the
  wrapper's stdout (`/proc/1/fd/1`) and print a fake "all tests passed" result.
- **Chosen:** the host generates a random nonce and sends it only on stdin. The wrapper keeps it in
  memory and wraps its JSON result in markers containing the nonce. The host accepts the result
  only if there is exactly one correctly tagged envelope and no other markers. Anything else is a
  `sandbox_violation`.
- **Alternative:** sign the result with a key. The key would have to live in the same container,
  so this is no stronger than a nonce here.

### D6. Use pytest with a custom plugin to report results
- **Chosen:** tasks use plain pytest tests. A plugin baked into the image writes a JSON report that
  includes each failure's exception type, base classes, and errno. It also imports `solution`
  itself at session start, so import-time failures keep their real exception type.
- **Alternatives:** parse pytest's text output (brittle); JUnit XML (parsing untrusted XML on the
  host invites XML-bomb problems and loses the exception type); a custom test runner (reinvents
  pytest, and task authors would need to learn it).
- **Known gap:** the plugin runs in the same process as the untrusted code, so deliberately
  adversarial code can monkeypatch it. Mitigations: the expected-test-count check, the
  exactly-one-envelope check, and the import-before-collection ordering. Fully closing the gap
  requires running tests out of process (call the solution over a pipe/RPC from a separate test
  process). That changes the task format, so it's a later decision.

### D7. Eight result categories with fixed precedence
`passed`, `wrong_answer`, `runtime_error`, `syntax_error`, `timeout`, `resource_limit`,
`sandbox_violation`, `harness_error`. Classification is a pure function (`scoring.classify`), and
the precedence is documented at the top of that file.
- `wrong_answer` means every failure is a failed test expectation: `AssertionError`, or pytest's
  `Failed` (raised by `pytest.raises` when nothing is raised). This was found in validation:
  `parse_duration`'s incorrect solution first came out as `runtime_error`.
- `sandbox_violation` covers PermissionError, EROFS/EACCES/EPERM, an unreachable network, DNS
  failure, a forged or missing envelope, output over the host read limit, and a test-count mismatch.
  The tasks never need any of these, so hitting one means the code tried to cross a boundary.
- `resource_limit` covers being OOM-killed, MemoryError, EAGAIN from fork (the pids limit), and
  ENOSPC (tmpfs full).
- `harness_error` never counts against the model. Milestone 2 will exclude it from the pass-rate
  denominator and report it separately.

### D8. Validate every task before scoring it, on every run
- **Chosen:** `evaluate()` runs each task's reference solution (must pass) and incorrect
  solutions (must fail on the merits: `wrong_answer` or `runtime_error`) before scoring
  samples, and refuses to score a task that fails. The reference run also fixes the
  expected test count.
- **Alternative:** cache validation by content hash. That's faster, but it's more state to explain and
  can go stale if the sandbox image changes. At 5 tasks the cost is about 1.5 s per task.

### D9. A mock model that sees only the prompt
- `MockModel` looks up the task by the exact prompt text instead of being handed the task id. This
  keeps the `Model` interface identical to what a real API adapter will implement (prompt in,
  text out). Ids are `mock/<behavior>@<version>`, `is_mock` is true on every record, and
  the CLI prints a banner above and below the results.
- The `mixed` behavior uses `random.Random(f"{seed}:{task}:{n}")`, so a seed reproduces exactly.

### D10. Five original sample tasks, each with a deliberately subtle wrong solution
`rle_encode`, `merge_intervals`, `balanced_brackets`, `top_k_words`, `parse_duration`. These are
familiar problem *types*, but the wording, edge cases, and tests are new. Each wrong solution
encodes a realistic bug (dropping the final run, assuming sorted input, counting brackets
instead of using a stack, no alphabetical tie-break, accepting units out of order). A familiar
problem type means models have likely seen similar problems, which matters for a real benchmark
but not for testing the harness (see the final decision brief).

### D11. Stdlib-only runtime, with `uv` for environments
No runtime dependencies in Milestone 1 (argparse, tomllib, subprocess). That keeps the harness easy
to audit and the sandbox image tiny. Dev tools (pytest, ruff) are repo-local through `uv`.

### D12. Leave the Docker context alone; use `DOCKER_CONTEXT=default` per command
The active context (`desktop-linux`) wasn't running. Switching the global context would change a
setting outside the repo, so commands pass the environment variable instead.
