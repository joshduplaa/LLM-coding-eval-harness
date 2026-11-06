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

### D12. Docker context: switched the machine's active context to `default` (revised)
The active context (`desktop-linux`, Docker Desktop) wasn't running; the native Engine
(`default`, `/var/run/docker.sock`) was. At first I left the global setting alone and prefixed every
command with `DOCKER_CONTEXT=default`, because changes outside the repo needed approval. Once
Joshua allowed work outside the repo, I ran `docker context use default`. Commands, tests, and
the Makefile now work without the prefix. To revert: `docker context use desktop-linux`. The
harness itself still honors `DOCKER_CONTEXT`/`DOCKER_HOST`, since it passes the CLI's environment
through to the `docker` CLI (never into the container).

### D13. gVisor as an opt-in sandbox runtime; runc stays the default
- **Chosen:** the OCI runtime is a setting (`--runtime`, `EVALHARNESS_SANDBOX_RUNTIME`). It is
  `runc` by default, and `runsc` (gVisor) when asked for. It's always passed explicitly to
  `docker run`, so a changed daemon default can't silently swap it. Every result records the
  runtime requested, the runtime Docker reports it used, and the pids limit actually applied.
- **Why opt-in instead of the default:** the harness must still work on machines without gVisor
  (GitHub's CI runners, a reviewer's laptop). Validation is about 55% slower under gVisor
  (0.87 s vs 1.36 s per container on this machine). The full suite passes under both, so either
  is a valid choice. The recommended setting for real untrusted models is `runsc`.
- **What gVisor adds:** with runc, the code's syscalls go straight to the host kernel, filtered
  only by seccomp, so one kernel bug can mean an escape. With gVisor they go to the Sentry, a
  user-space kernel written in Go, which itself uses only a small, seccomp-restricted set of host
  syscalls. An attacker now needs two bugs, one in gVisor and one in the host kernel.
- **Difference found in testing:** under gVisor, `--pids-limit` limits the *Sentry's host
  threads*, not the processes inside. At pids=32, a fork bomb starved the Sentry, which crashed
  (exit 2, no output). The classifier correctly refused to trust that and recorded
  `sandbox_violation`, but for the wrong reason. Measured threshold: crashes at 48, fine at 56.
  **Fix:** under runsc, the pids limit is raised to at least 256 (`RUNSC_MIN_PIDS`), and the
  memory cgroup stops fork bombs instead (they now come out as `resource_limit`, OOM-killed).
- **Probe test difference:** `/proc/self/status` inside gVisor shows `Seccomp: 0`, because
  gVisor applies Docker's seccomp profile internally and doesn't report it there. So under
  runsc, the probe instead checks that the kernel it sees is *not* the host kernel.
- **Install:** apt package `runsc` from gvisor.dev, `runsc install`, then restart Docker. It's
  root-owned in `/usr/bin` on purpose. A runtime binary in a user-writable directory would let
  any process running as Joshua replace code that dockerd runs as root.

## Milestone 2 — Results, metrics, and API

### D14. SQLite store: full JSON records plus a few indexed columns, append-only
- **Chosen:** `results/evalharness.sqlite` (git-ignored), via Python's built-in `sqlite3`. Each
  run's document and each complete sample record are stored as JSON text. Fields we filter on
  (model, task, category, test counts, duration) are copied into real columns with an index.
  A run is inserted once, in one transaction, and never updated; inserting the same run again
  fails. The JSON file in `runs/` is still written, so the database can always be rebuilt with
  `evalharness import runs/*.json`.
- **Alternatives:** (a) JSON files only: simple, but every question means loading every file.
  (b) Fully normalized tables (one per field group): every new record field becomes a
  migration, and it's easy to drop a field that reproducibility needs. (c) Postgres: real
  concurrency and a server to run, which a single-machine harness doesn't need yet.
- **Why:** the JSON column guarantees nothing needed to reproduce a sample is lost, and the
  columns make counting and filtering plain SQL. Append-only means a stored result can't be
  quietly edited later. Schema version lives in `PRAGMA user_version`, and an unknown version
  is refused instead of misread.
- **Safety:** every value goes in through `?` parameters, never string formatting, so model
  output like `'); DROP TABLE runs; --` is stored as text (tested).

### D15. Pass rate = passed / scored, with n, a Wilson 95% interval, and exclusions shown
- **Denominator:** every category except `harness_error`. Excluded samples are always reported
  as `n_excluded`, and a summary is flagged when more than 5% are excluded (see the Milestone 1
  checkpoint: the exclusion bucket is an attack surface if it's hidden).
- **Interval:** Wilson score, not the textbook Wald interval `p ± 1.96·sqrt(p(1-p)/n)`. Wald gives
  [100%, 100%] for 5/5, claiming certainty from five samples, and can go outside [0, 1]. Wilson
  gives [56.6%, 100%] for 5/5. Tested against published values and an independent derivation.
- **Stated limitation:** the overall interval treats samples as independent. Several samples of
  the same task are correlated (a model that can't do a task fails it repeatedly), so the real
  uncertainty is wider. Every report prints this note. A clustered or bootstrap-over-tasks
  interval is the fix if we ever report overall rates with many samples per task.
- **No comparisons:** metrics describe one run. Nothing in the code ranks models.

### D16. A read-only FastAPI service, bound to localhost by default
- **Chosen:** FastAPI + uvicorn. Endpoints: `/healthz`, `/tasks`, `/runs`, `/runs/{id}`,
  `/runs/{id}/metrics`, `/runs/{id}/samples` (filter by task and category, paginated), and
  `/runs/{id}/samples/{task}/{index}` (the full record). Every response about a mock run includes
  `is_mock: true` and the MOCK notice, so the label travels with the data.
- **Read-only:** no endpoint starts a run. Starting runs over HTTP means the service starts
  sandboxes, and how to do that safely in Kubernetes is the Milestone 4 design question.
- **Alternatives:** Python's built-in `http.server` (no dependencies, but no input validation
  or generated docs); Flask (fine, but FastAPI validates query parameters from type hints, for
  example a bad `category` gets a 422, and produces OpenAPI docs at `/docs`).
- **Cost:** the first runtime dependencies (fastapi, uvicorn). The harness core (sandbox,
  scoring, runner) still imports none of them.
- **Supply-chain check:** a deprecation warning said to install `httpx2` instead of `httpx` for
  the test client. A near-identical package name is a classic typosquatting pattern, so before
  installing I confirmed the advice is in Starlette's own source and that `httpx2` on PyPI has
  the same author as `httpx`, maintained under the pydantic organization.

## Milestone 3 — Images and CI

### D17. Two images with separate jobs: the sandbox runs untrusted code, the service never does
- **Service image** (`Dockerfile` at the repo root): the read-only API. Multi-stage build. The
  first stage installs dependencies with `uv sync --locked` (exact versions from `uv.lock`). The
  final stage copies only the virtualenv and the tasks: no uv, no build tools, no source tree,
  no `.git`. It runs as uid 10002 (a different user from the sandbox's 10001), has a Docker
  `HEALTHCHECK` against `/healthz`, and keeps its database on a `/data` volume.
- **No Docker inside the service image.** It can't start sandboxes, which matches the
  read-only API (D16). Giving a container the Docker socket is effectively giving it root on
  the host, so that's a deliberate Milestone 4 decision, not a default.
- **Allowlist `.dockerignore`:** start from "ignore everything" and list what's needed. A
  denylist fails open: forget one pattern and `.env` is in the build context. An allowlist fails
  closed. The smoke test checks that `.env`, `.git`, `results/`, `runs/`, and `uv` are absent.
- **Pinned base images:** `python:3.12-slim@sha256:…` and `uv:0.10.2@sha256:…`. A tag like
  `3.12-slim` is re-pointed whenever Debian or Python ships a patch, so the same Dockerfile
  could build different images. The sandbox image ID is recorded on every result (D1), so
  pinning makes "same image" mean something. Cost: security patches only arrive when someone
  bumps the digest, so it needs a periodic update (Dependabot can do it later).
- **`--host 0.0.0.0` inside the container:** inside a container, `127.0.0.1` is the container's
  own loopback, which Docker's port publishing can't reach. The exposure decision moves to
  `docker run -p 127.0.0.1:8000:8000`, which publishes to this machine only.

### D18. CI: three parallel jobs, read-only token, no secrets, no deploy
- **Jobs:** `lint` (ruff check plus format check), `test` (builds the sandbox image, then the
  *whole* suite including the adversarial Docker tests, then checks no containers were left),
  and `images` (builds both images, runs the smoke test). They run in parallel, so a lint error
  doesn't hide a test failure.
- **Security tests can't be skipped silently:** CI sets `EVALHARNESS_REQUIRE_DOCKER=1`, which
  turns "sandbox unavailable" into an error instead of a skip (conftest).
- **Least privilege:** `permissions: contents: read` and `persist-credentials: false`. The job
  token can't push, comment, or publish, and isn't left on disk for later steps. No `secrets.*`
  appears anywhere in the workflow.
- **Supply chain:** third-party actions are pinned to full commit SHAs, with the version in a
  comment. In 2025 the popular `tj-actions/changed-files` action had its tags re-pointed to
  malicious code, and workflows pinned by SHA were unaffected. Only two actions are used
  (`checkout`, `setup-uv`); images are built with plain `docker build` rather than another action.
- **Checked locally first:** `make ci` runs the same steps, and `make lint-workflows` runs
  actionlint (pinned image) to catch workflow syntax and expression mistakes before pushing.
- **Not in CI (yet):** gVisor (needs installing runsc on the runner; D13 keeps runc the default),
  image publishing (that's a deploy concern), and real model calls (not approved).

### D19. Versioning: one package version per milestone, tagged at the merge
- **Package version** (`pyproject.toml`, `evalharness.__version__`) follows semantic versioning:
  `MAJOR.MINOR.PATCH`. Below 1.0 the interface isn't promised stable yet, so each milestone bumps
  MINOR: v0.1.0 = Milestone 1, v0.2.0 = Milestone 2, v0.3.0 = Milestone 3. A test keeps the two
  copies of the number equal, because every stored result records it.
- **Git tags** `vX.Y.Z` are annotated tags on the merge commit in `main`, so a version names one
  exact, findable state of the code. Results also record the git commit, which is what precise
  reproduction uses. The version is the human-readable label.
- **Service image tag = package version** (`evalharness-service:0.3.0`). The smoke test checks
  that `/healthz` in the image reports the same version as its tag.
- **The sandbox image has its own version** (`evalharness-sandbox:0.1.0`). Its contents (the
  wrapper, the plugin, pytest, the base image) change rarely and independently of the API. Every
  result records the sandbox image's content ID (`sha256:…`), which is exact even if a tag is
  reused. Bump the sandbox tag whenever the wrapper, plugin, or pytest changes.
- **Mock model ids** (`mock/mixed@0.1.0`) are versioned separately too, and change only if mock
  behavior changes.
- **Workflow:** one branch per milestone, merged before the next starts, so branches don't
  stack. (Milestone 3 was briefly stacked on Milestone 2. It worked, but it was confusing for no
  benefit.)
