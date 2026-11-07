# Decision brief (end of Milestone 6)

Everything the brief asked for has been built and checked with a **mock model** only. The six
questions below are decisions for Joshua. Each one has a recommendation, the cost, and the
tradeoffs. Nothing here has been acted on: no API key, no paid call, no cloud resource.

Prices checked on 2026-11-07. Claude prices come from Anthropic's current price list. Azure
prices come from the Azure Retail Prices API (westus2, pay-as-you-go, Linux). Other providers'
prices come from third-party aggregators that disagree with each other, so treat those as
**unverified**. Token counts are estimates until one real run measures them.

---

## 1. Which benchmark or task set, and is its license acceptable?

**Recommendation: HumanEval+ (`evalplus/humanevalplus`, 164 Python tasks) for the first public
run, with the five original tasks kept as a smoke set.**

| Option | Size | License (Hugging Face card) | Notes |
|---|---|---|---|
| **HumanEval+** | 164 | Apache-2.0 (built on MIT-licensed HumanEval) | ~80× more tests per task than HumanEval, so fewer false passes. Single-function tasks fit our task format. |
| MBPP+ | 378 | Apache-2.0 (built on CC-BY-4.0 MBPP) | A good second set. CC-BY needs attribution. |
| HumanEval | 164 | MIT | Too few tests per task; many wrong solutions pass. |
| BigCodeBench | 1,140 | Apache-2.0 | More realistic, but needs ~70 third-party libraries in the sandbox image, and some tasks expect network or file access our sandbox blocks on purpose. |
| LiveCodeBench | varies | Tagged only "cc" (variant unclear) | Problems come from LeetCode, AtCoder, and Codeforces, whose terms are separate. **Not acceptable until the license is clarified.** |

- **License:** Apache-2.0 and MIT both allow redistribution and commercial use with attribution
  and the license text. **Acceptable.** We'd keep a `LICENSES/` note and credit EvalPlus and OpenAI.
- **Tradeoffs:** HumanEval is from 2021 and is very likely in every model's training data
  (contamination), so high scores partly measure memorization. That's acceptable for showing
  the harness works, but any published number must state it. A contamination-resistant set
  (dated problems) is the upgrade later, once its license is clear.
- **Work needed:** an importer that converts each HumanEval+ task into our format
  (`task.toml` + pytest tests + reference). The `validate` gate then proves every converted test
  suite passes its reference and fails an incorrect solution. HumanEval+ has no incorrect
  solutions, so we'd generate one per task (for example, the reference with its return value
  changed) and check it is rejected. Estimate: about a day.
- **Cost:** $0.

## 2. Which models should we compare?

**Recommendation: three Claude tiers from one provider: `claude-haiku-5-5`, `claude-sonnet-5-5`,
and `claude-opus-5-5`.** One API key, one SDK, and one set of rate limits and terms keeps the
first real run simple. Reports describe each model with its own interval and make no ranking
claims, as the brief requires.

Estimated cost of one HumanEval+ run, **5 samples per task (820 samples per model)**. The
estimate assumes ~400 input tokens and ~1,500 output tokens per sample (thinking included). It
is deliberately high; the first real run replaces it with measured numbers.

| Model | $ / M input | $ / M output | Per sample | Per run (820) |
|---|---|---|---|---|
| `claude-haiku-5-5` | 0.10 | 0.50 | $0.0008 | **≈ $0.65** |
| `claude-sonnet-5-5` | 2.00 | 10.00 | $0.016 | **≈ $13** |
| `claude-opus-5-5` | 4.00 | 20.00 | $0.032 | **≈ $26** |
| All three | | | | **≈ $40** |

- The Message Batches API halves this (≈ $20), but results arrive later and the runner would
  need a batch mode. It's worth adding once runs get bigger.
- **Settings to fix and record:** `effort` (Opus 5.5 defaults to `medium`, Sonnet 5.5 to `high`,
  so set it explicitly and the same for all three) and `max_tokens`. Temperature can no
  longer be set on current models. Variation therefore comes only from repeated samples, which
  is why we take 5 per task.
- **Alternatives:** add one model from another provider (for example an OpenAI GPT-5.x model;
  aggregator prices run about $2–5 / M input and $12–30 / M output, unverified), or an
  open-weights model run locally (no API cost, but GPU time and setup). Both make the comparison
  more interesting and the first run more complicated: a second key, a second terms of service,
  and different parameters to keep comparable.
- **Work needed:** a real model adapter (`models.py` has only mocks), reading the key from the
  environment (never logged), and recording the provider's returned model id and token usage.

## 3. What budget and spending limits should apply?

**Recommendation: $60 for the first public evaluation, enforced in three places.**

1. **At the provider:** a monthly spend limit of **$100** on the Anthropic Console workspace
   that owns the key. This is the hard backstop, since it holds even if the harness has a bug.
2. **In the harness (needs building):** a `--max-cost-usd` guard. Before sending a request, it
   adds the worst-case cost (input tokens + `max_tokens` × price) to the running total and stops
   the run cleanly if the cap would be exceeded. Spent dollars get recorded in the run record.
3. **Staged spending:** first a shakedown on the 5 original tasks with Haiku (< $0.05), then one
   model on HumanEval+, then the rest. Every stage needs a separate approval.

- **Cost:** the limits are free; the evaluation itself is ≈ $40 by the estimate above, under a
  $60 cap.
- **Tradeoffs:** a tight cap can stop a run halfway. The guard must then mark the run
  `incomplete` and never report a partial pass rate as final. A dedicated key for this project,
  with its own workspace limit, keeps a leak or bug from touching anything else.

## 4. Which cloud provider, if any?

**Recommendation: none for the first evaluation. Run it locally (Docker or kind) for $0. Keep
the Azure Terraform as a reviewed draft and apply it only for a demo, then destroy it.**

The harness only needs one machine: the slow part is waiting for the model API, not running
tests. A full HumanEval+ run is ~820 sandboxes per model, ~1 s of compute each.

Azure as drafted (`infra/azure`, westus2) costs about this much when **left running**:

| Item | Price | Per month |
|---|---|---|
| AKS control plane, Free tier | $0 (the price list shows a $0.05/h "FreeTierInfrastructure" meter; confirm in the calculator) | $0 (or $36) |
| System pool, 1 × D2s_v5 | $0.096/h | $70 |
| Apps pool, 1 × D2s_v5 | $0.096/h | $70 |
| Sandbox pool, D4s_v5, 0 nodes when idle | $0.192/h while running | ≈ $0.20 per eval |
| 2 OS disks (128 GB premium, P10) | $17.92 each | $36 |
| Container Registry, Standard | $0.667/day | $20 |
| Standard load balancer + public IP | IP $0.005/h; LB unverified (~$0.025/h) | ≈ $22 |
| **Total** | | **≈ $220 / month (≈ $7 / day)** |

- `terraform destroy` takes it to $0. Stopping the cluster (`az aks stop`) still leaves the
  registry, disks, and IP at about $60/month.
- **Tradeoffs:** Azure adds a real demo URL and real Kata VM isolation per sandbox (stronger than
  local Docker). It also brings cost, an access setup (Entra ID, OIDC federated credential, an
  `azure` environment with required reviewers), and quota requests. SQLite on one disk also has
  to become a managed database before two pods can share results across nodes (D22).
- **If we do deploy:** an Azure budget alert at $25 and $50 on the resource group, and a written
  rule to destroy after each demo.
- **Alternatives:** AWS or GCP would mean rewriting the Terraform. Not worth it unless an
  employer you're targeting uses one of them.

## 5. What question should the first public evaluation answer?

**Recommendation:** *"On HumanEval+, how often does each of three Claude models produce code
that passes the full hidden test suite in a locked-down sandbox, with 95% intervals, and when it
fails, how does it fail?"*

- The interesting, defensible part is the **failure breakdown** (wrong answer vs. runtime error
  vs. timeout vs. sandbox violation), which most leaderboards don't show. It's what this harness
  does differently.
- It stays descriptive. The report shows each model's rate with its interval and sample size and
  makes no "X is better" claim, as the brief requires. Where intervals overlap, the report should
  say the data can't separate them.
- **Tradeoffs:** a contaminated benchmark limits what the numbers mean. The write-up has to say
  so, and say that 164 tasks × 5 samples are correlated, so the real interval is wider than
  Wilson's (D15). A clustered or bootstrap-over-tasks interval should land before publishing.
- **Cost:** the run in question 2 (≈ $40).

## 6. What is ready to publish, and what still needs work?

**Ready now** (the repo is already public):
- The sandbox: Docker and Kubernetes backends, adversarial tests (escape attempts, infinite
  loops, memory, process and output bombs, network, harness tampering), and the egress-gate
  finding (D20).
- Scoring with 8 distinct categories, Wilson intervals, run records that can be reproduced,
  the results API, and Prometheus/Grafana with mock data labeled on every panel.
- CI with four required checks, the Azure Terraform draft with offline tests, and a deploy
  workflow that is disabled by default.
- The README, decisions log (D1–D23), and these docs. A portfolio write-up can describe all of
  this today, as long as it says every result is from a mock.

**Needs work before any real model result is published:**

| Gap | Why it matters | Size |
|---|---|---|
| Real model adapter + key handling | Nothing can call a model yet | ½ day |
| `--max-cost-usd` guard + cost in run records | Budget enforcement inside the harness (question 3) | ½ day |
| HumanEval+ importer + generated incorrect solutions | The benchmark (question 1) | 1 day |
| Clustered / bootstrap interval for overall rates | Wilson treats correlated samples as independent (D15) | ½ day |
| Parallel sample execution | 820 serial samples × ~3 s ≈ 40 min per model; fine, not urgent | ½ day |
| Tests run in-process with the untrusted code (D2) | Adversarial code can tamper with the pytest plugin; mitigated, not closed | 1–2 days |
| Evaluator could forge results if compromised (D20) | Matters only on a shared cluster | later |
| SQLite → managed DB, tfsec/checkov in CI (D22) | Only if Azure is used | later |

**Suggested order if you approve:** questions 1–3 together, then the work in the first four
rows, then the Haiku shakedown (< $0.05), then the full run.
