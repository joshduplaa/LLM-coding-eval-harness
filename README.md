# LLM-coding-eval-harness

A harness that runs model-generated code against test suites inside a locked-down sandbox and
records reproducible, categorized results. The goal is to package it with Docker, provision it with
Terraform, deploy it through GitHub Actions, and monitor pass rates with Grafana. Those parts are
built milestone by milestone; see [docs/PLAN.md](docs/PLAN.md).

> **Status: Milestone 1 (core harness).** Only a **mock model** exists. Every result it produces
> is synthetic and labeled `MOCK`. It is not a measurement of any real model.

## Quick start

Requirements: Linux or macOS, Docker, [uv](https://docs.astral.sh/uv/).

```bash
make install          # .venv with the package + pytest + ruff
make sandbox-image    # build evalharness-sandbox:0.1.0
uv run evalharness list-tasks
uv run evalharness validate                       # every task: reference passes, incorrect fails
uv run evalharness run --model mock-reference     # end to end; writes runs/<run_id>.json
uv run evalharness run --model mock-mixed --samples 3 --seed 1
make test             # 68 tests, including adversarial sandbox tests
```

If Docker Desktop isn't running but the native engine is, prefix commands with
`DOCKER_CONTEXT=default`.

## Docs
- [docs/PLAN.md](docs/PLAN.md): architecture and milestones
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions, alternatives, and tradeoffs
- [docs/LEARNING.md](docs/LEARNING.md): concepts explained
- [docs/INTERVIEW_NOTES.md](docs/INTERVIEW_NOTES.md): problems hit and how they were solved
