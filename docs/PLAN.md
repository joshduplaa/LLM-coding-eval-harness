# Plan

A Python harness that sends coding tasks to a model, runs the generated code
against each task's tests inside a locked-down container, and records
reproducible, categorized results. It gets built one milestone at a time, with
a teaching checkpoint after each.

## Starting point (inspected 2026-11-04)

- Repo: one commit with a two-line README. Work happens on feature branches. Nothing goes to `main` or GitHub without approval.
- Available: Python 3.12 through `uv`, Docker Engine 29.5 (cgroup v2, seccomp and AppArmor on) on the `default` context, `gh`.
  Docker Desktop (`desktop-linux` context) wasn't running, so the active context was switched to
  `default` (see D12).
- Missing: `kind`, `kubectl`, `helm`, `terraform`, `ruff`. Ruff is a repo-local dev dependency.
  The rest will be installed as user-local binaries in `~/.local/bin` (already on PATH, no sudo)
  when Milestones 4 and 5 need them.
- No passwordless sudo, so anything needing root (e.g. installing gVisor) needs Joshua to run it.

## Architecture (Milestone 1)

```
tasks/<id>/task.toml + tests/ + reference/   ──► tasks.py (load + hash)
                                                     │
CLI (cli.py) ──► runner.py ──► models.py (MockModel) ─┤ prompt ► completion ► extracted code
                     │                                │
                     └──► sandbox.py ── docker run (no network, read-only, non-root, limits)
                                │           └─ /opt/harness/entrypoint.py (trusted wrapper)
                                │                 ├─ syntax check (child process)
                                │                 └─ pytest + harness plugin → JSON report
                                ▼
                         scoring.py (pure function: raw sandbox outcome → category)
                                ▼
                         run record JSON (everything needed to reproduce)
```

Result categories: `passed`, `wrong_answer`, `runtime_error`, `syntax_error`, `timeout`,
`resource_limit`, `sandbox_violation`, `harness_error`. A `harness_error` is never
counted as a model failure.

## Milestones

1. **Core harness.** Task format, sandbox image and runner, scorer, mock model, CLI
   (`list-tasks`, `validate`, `run`), five original sample tasks, each with a reference
   solution and an intentionally wrong one. Tests cover the classifier, the loader,
   code extraction, and adversarial sandbox behavior (escape attempts, infinite loops,
   memory and process bombs, output floods, harness tampering).
2. **Results and service.** SQLite results store, metrics (pass rate with a Wilson 95% CI,
   sample sizes, counts per category), and a small FastAPI service.
3. **Packaging and CI.** Harness service image, plus a GitHub Actions workflow that runs
   lint, tests (sandbox tests included, since GitHub runners have Docker), and image builds.
   The workflow uses no secrets and deploys nothing.
4. **Local deployment and monitoring.** kind cluster, Prometheus metrics endpoint, and a
   Grafana dashboard. Needs approval to install kind and kubectl. The big design question
   is how the service starts sandboxes from inside Kubernetes; it is settled at that point.
5. **Infrastructure draft.** Terraform for a provisional target, checked only with
   `terraform fmt` and `terraform validate`. Includes a deploy workflow that is disabled by default.
6. **Handoff.** An accurate README covering setup, architecture, security limits, and known gaps,
   plus the decision brief.

## Out of scope unless needed

Real model APIs (need approval), multiple languages, parallel execution, authentication,
and a web UI.
