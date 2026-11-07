# LLM-coding-eval-harness

A harness for evaluating code written by language models. It sends a coding task to a model,
runs the code it returns against the task's hidden tests **inside a locked-down, disposable
sandbox**, and records a reproducible, categorized result: passed, wrong answer, runtime error,
syntax error, timeout, resource limit, sandbox violation, or harness error. Pass rates are always
reported with their sample size and a 95% interval.

> **Status (v0.6.0): all six milestones are done, using a mock model only.** No real model has
> been called. Every result the harness produces today is synthetic and labeled `MOCK`. It is
> **not** a measurement of any real model. Real model calls, the benchmark, the budget, and any
> cloud deployment wait on the decisions in [docs/DECISION_BRIEF.md](docs/DECISION_BRIEF.md).

**Contents:** [Quick start](#quick-start) · [How it works](#how-it-works) ·
[Security model and limits](#security-model-and-limits) · [Known gaps](#known-gaps) ·
[Kubernetes and dashboard](#local-kubernetes-and-dashboard) · [Azure draft](#azure-draft-never-applied) ·
[CI](#ci) · [Configuration](#configuration) · [Docs](#docs)

## Quick start

**Requirements:** Linux (or macOS with Docker Desktop), Docker, [uv](https://docs.astral.sh/uv/),
and `make`. Kubernetes and Terraform are optional (see below).

```bash
git clone https://github.com/joshduplaa/LLM-coding-eval-harness.git
cd LLM-coding-eval-harness
make install          # creates .venv with the package, pytest, and ruff
make sandbox-image    # builds evalharness-sandbox:0.2.0, the image untrusted code runs in
```

Then try it:

```bash
uv run evalharness list-tasks                     # the five sample tasks
uv run evalharness validate                       # every task: reference passes, incorrect fails
uv run evalharness run --model mock-mixed --samples 3 --seed 1
uv run evalharness runs                           # stored runs
uv run evalharness report <run_id>                # the report again (--json for machines)
uv run evalharness serve                          # read-only API at http://127.0.0.1:8000/docs
```

The `run` command prints one line per sample, then pass rates per task and overall with
95% intervals. The full record goes to `runs/<run_id>.json` and `results/evalharness.sqlite`.

**Mock models** (no network, no cost): `mock-reference` returns each task's correct solution,
`mock-incorrect` the intentionally wrong one, and `mock-mixed` picks between correct, wrong, and
broken code with a seeded random choice, so every result category shows up.

**Tests:**

```bash
make test-unit        # fast, no Docker
make test             # everything, including the adversarial sandbox tests
make ci               # what GitHub Actions runs: lint, workflow lint, Terraform checks, images, tests, smoke
```

If `docker info` fails because your active Docker context isn't running (for example, Docker
Desktop is stopped), run `docker context use default` or set `DOCKER_CONTEXT` for one command.
On a machine with little memory, cap a full run:
`systemd-run --user --scope -p MemoryMax=8G make ci`.

## How it works

```
tasks/<id>/  task.toml + tests/ + reference/
     │
     ▼
 tasks.py ── load, check, hash ──► runner.py ──► models.py (mock) ──► prompts.py: extract code
                                       │
                                       ▼  one fresh sandbox per sample
                           sandbox.py (Docker)  or  k8s_sandbox.py (one pod per sample)
                                       │   files + a random nonce go in over stdin
                                       ▼
                           sandbox/entrypoint.py  (trusted wrapper, PID 1, non-root)
                             ├─ syntax check in a child process
                             └─ pytest + harness plugin ──► one nonce-tagged JSON result
                                       │
                                       ▼
                           scoring.py  (pure function: raw outcome ──► one category)
                                       │
                ┌──────────────────────┼─────────────────────────┐
                ▼                      ▼                         ▼
        runs/<run_id>.json     store.py (SQLite)       metrics.py (Wilson 95% CI)
                                       │
                                       ▼
                     api.py: read-only FastAPI + /metrics/ ──► Prometheus ──► Grafana
```

- **Tasks** are directories. `task.toml` holds the id, version, signature, description, and time
  limit. `tests/` holds pytest tests the model never sees. `reference/` holds a known-correct
  `solution.py` and at least one intentionally wrong `incorrect_*.py`. `evalharness validate` proves that every
  test suite accepts the reference and rejects the incorrect one (D8).
- **Each run record** stores what's needed to reproduce it: task id, version, and content hash,
  model id and parameters, the exact prompt and prompt-template version, the generated code,
  the test output, exit status, duration, the sandbox backend and runtime, and the harness
  version (D9).
- **Categories are distinct and ordered** (D3). A `harness_error` (our fault: image missing,
  daemon down, tampered result) is never counted as a model failure. It is excluded from the pass
  rate and reported separately.
- **Statistics** (D15): pass rate = passed ÷ scored samples, with a Wilson 95% interval and `n`.
  Reports note that several samples of one task are correlated, so the real uncertainty is wider.
  Nothing ranks or compares models.

## Security model and limits

The rule: **all model-generated code is untrusted and never runs on the host.** It runs only
inside a fresh sandbox that is destroyed afterwards.

**Docker backend (default):** every sample gets its own container with

| Control | Setting |
|---|---|
| Network | `--network none` |
| Filesystem | `--read-only` root. `/tmp` is a 32 MB tmpfs mounted `noexec,nosuid,nodev`. No host mounts, no secrets, no environment passed in. |
| User | non-root UID, `--cap-drop ALL`, `no-new-privileges`, `--ipc none` |
| CPU / memory | 1 CPU, 256 MB with swap disabled |
| Processes / files | `--pids-limit 64`, 256 open files |
| Time | per-task limit (default 10 s); the host kills the container after it |
| Output | the host stops reading at 1 MB and kills the container |
| Kernel isolation (optional) | `--runtime runsc` runs the code under [gVisor](https://gvisor.dev)'s user-space kernel (~0.5 s more per sample, D13) |

Files reach the sandbox over stdin, not a bind mount, and the result comes back on stdout tagged
with a per-sample random nonce. Code that prints a fake result can't produce the right tag (D2).

**Kubernetes backend** (`EVALHARNESS_SANDBOX_BACKEND=kubernetes`): one pod per sample, in a
namespace with the `restricted` Pod Security Standard, a deny-all NetworkPolicy, and a resource
quota. Pods get no service-account token, no service links, and DNS pointed at nowhere. Before
running anything, the wrapper checks that egress is really blocked and fails closed if it isn't
(D20). On AKS, sandbox pods would run on a separate, tainted node pool, each in its own Kata VM.

**What the tests prove.** `tests/test_sandbox_security.py` runs hostile code and checks that each
attempt is contained *and* recorded as the right category: looking for the Docker socket,
secrets in the environment, or host mounts; network connections (also at import time) and DNS
lookups; writing to the root filesystem; setuid; infinite loops; memory bombs; fork bombs;
filling `/tmp`; flooding output; exiting before the tests run; deleting the tests; and forging
the result. The same suite runs under gVisor (`make test-gvisor`) and in kind pods
(`make test-k8s`).

**Secrets:** none are needed or used. `.env.example` documents the settings. `.env`, Terraform
state, and `*.tfvars` are git-ignored. CI and the deploy workflow store no secrets (deploying
would use OIDC).

## Known gaps

These are known and written down. None of them is hidden by a test.

- **Shared kernel.** Plain Docker containers share the host kernel, so a kernel exploit could
  escape. gVisor (local) or Kata (AKS) closes most of that (D1, D13).
- **Tests run in the same process as the untrusted code** (D2). Deliberately adversarial code
  can monkeypatch the pytest plugin. It's mitigated (expected test count, exactly one tagged
  result, import order) but not closed. Closing it means running tests in a separate process,
  which changes the task format.
- **Kubernetes sandbox is weaker than Docker in places** (D20): `/tmp` can't be `noexec`, there's
  no per-pod open-files limit, and the process limit is per node, not per pod. A hung blocked
  connection is recorded as `timeout`, not `sandbox_violation`.
- **The evaluator is trusted** (D20). RBAC confines it to sandbox pods, but a compromised evaluator
  could forge results or send data out.
- **Correlated samples** (D15): the overall interval treats samples as independent, so it's too
  narrow when there are many samples per task. A clustered or bootstrap interval should land before
  a real result is published.
- **No real model, no benchmark yet.** There is no API adapter, no spending guard, and no importer
  for a public benchmark (decision brief, question 6).
- **Serial execution.** Samples run one at a time, about 1 s each on Docker and about 3 s on kind.
- **Azure is untested against a real subscription** (D22): the RuntimeClass name, SQLite on a
  shared disk, and cost estimates all need checking before any `apply`.

## Local Kubernetes and dashboard

Needs [`kind`](https://kind.sigs.k8s.io) and `kubectl`. Everything runs on your laptop; nothing
reaches the cloud.

```bash
make deploy      # kind cluster; loads images; API + Prometheus + Grafana + sandbox namespace
make k8s-eval    # one MOCK evaluation as a Kubernetes Job; each sample in its own locked-down pod
make dashboard   # Grafana at http://127.0.0.1:3000 (anonymous, read-only); Ctrl-C to stop
make test-k8s    # the adversarial sandbox suite, run inside kind pods
make cluster-down   # delete the cluster (frees ~1.5 GB of memory)
```

![Grafana dashboard showing MOCK data](docs/img/dashboard-mock.png)

*Screenshot of mock data: synthetic output from `mock-mixed`, not a model result.* Every panel
shows `is_mock`, pass rates appear only with their interval and `n`, and only `passed` is green.
Grafana has no accounts and no login; viewers can't edit (D21).

## Azure draft (never applied)

`infra/azure` is Terraform for AKS: system, apps, and sandbox node pools (sandbox pool tainted,
Kata VM per pod, scales to zero), Entra ID-only access with local accounts off, an IP allowlist on
the API server, Cilium network policy, and a container registry with admin login disabled (nodes pull with a managed identity).

```bash
make tf-check    # terraform fmt + validate + offline tests (mock provider: no account, no cost)
```

`.github/workflows/deploy.yml` can deploy it, but it is **off three ways**: it runs only by hand,
only if the repository variable `EVALHARNESS_DEPLOY_ENABLED` is `true`, and only in the `azure`
environment. Its default action is `plan`. Left running, the cluster would cost about $220 a
month; see the decision brief, question 4, and D22–D23.

## CI

`.github/workflows/ci.yml` runs on every push and pull request with four required checks on
`main`: **lint** (ruff, actionlint), **test** (the full suite, including the adversarial sandbox
tests; GitHub runners have Docker), **images** (sandbox, service, and evaluator images plus a smoke
test of the API image), and **terraform** (fmt, validate, offline tests). It uses no secrets and
deploys nothing. Actions are pinned by commit SHA.

## Configuration

All optional. See `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `EVALHARNESS_SANDBOX_BACKEND` | `docker` | `docker` or `kubernetes` |
| `EVALHARNESS_SANDBOX_IMAGE` | `evalharness-sandbox:0.2.0` | image untrusted code runs in |
| `EVALHARNESS_SANDBOX_RUNTIME` | `runc` | `runsc` for gVisor (Docker backend) |
| `EVALHARNESS_DB` | `results/evalharness.sqlite` | results database |
| `EVALHARNESS_TASKS_DIR` | `./tasks` | task directory |
| `EVALHARNESS_SANDBOX_NAMESPACE`, `_PULL_POLICY`, `_RUNTIME_CLASS`, `_NODE_POOL` | kind defaults | Kubernetes backend; the AKS overlay sets them |

Versions: the package, service, and evaluator images share one version (0.6.0). The sandbox
image is versioned separately (0.2.0), because changing it can change results (D19).

## Docs

- [docs/DECISION_BRIEF.md](docs/DECISION_BRIEF.md): the open decisions, with recommendations, costs, and tradeoffs
- [docs/PLAN.md](docs/PLAN.md): milestones
- [docs/DECISIONS.md](docs/DECISIONS.md): every design decision (D1–D23), alternatives, and known gaps
- [docs/LEARNING.md](docs/LEARNING.md): the concepts, explained
- [docs/INTERVIEW_NOTES.md](docs/INTERVIEW_NOTES.md): problems hit and how they were solved
