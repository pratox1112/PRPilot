# PRPilot

A GitHub App that triages new pull requests with a fine-tuned 1.7B model. When a PR opens,
PRPilot proposes its type, labels, reviewers and related issues, with a short summary,
rationale and risk. It writes to GitHub only after a person approves the exact proposal.
Untrusted PR text is screened for prompt injection, personal data and secrets before any
model sees it.

The model is Qwen3-1.7B, fine-tuned with LoRA on labels from a 122B teacher, and served as
a 1.05 GiB Q4_K_M GGUF on CPU. It is scored against what maintainers actually did on 9,573
held-out PRs from cilium, kubernetes and pandas, plus numpy, a repo it never saw in training.

## Results

Every number links to the file it comes from, recorded on a clean tree at the commit shown.
The test split was frozen before training and scored once per system. Maintainer match means
the predicted PR type equals the type the maintainers labelled, on the 5,562 test PRs that
have one.

### Offline, on the test split

| System | Maintainer match | Notes | Source |
|---|---|---|---|
| **Student** (Qwen3-1.7B + LoRA, bf16) | **70.3%** (CI 69.1–71.5) | valid JSON 98.7% on the first attempt, unconstrained; 0.048 GPU-s per PR | [student_test.json](results/student_test.json) @ 0cf4899 |
| Student as deployed (Q4_K_M GGUF, with retries) | 69.7% (CI 68.5–70.9) | −0.7 points vs bf16 (p=0.03); the loss is almost all numpy | [student_gguf_test.json](results/student_gguf_test.json) @ 3baac29 |
| Teacher (Qwen3.5-122B-A10B, GPTQ-Int4) | 65.0% (CI 63.8–66.3) | 0.951 GPU-s per PR, about 20 times the student | [student_test.json](results/student_test.json) @ 0cf4899 |
| Rules baseline | 53.0% | | [student_test.json](results/student_test.json) @ 0cf4899 |
| Untuned Qwen3-1.7B | 36.1% | | [student_test.json](results/student_test.json) @ 0cf4899 |

- **The student beats its teacher.** McNemar test: 481 vs 191 discordant PRs, p<1e-6.
- **It ties Gemini 3.8 Flash.** On a seeded 500-PR test sample (n=279 typed), the student with
  retries and Gemini with retries both reach 68.1% (19 vs 19, p=1.0). Gemini cost $0.0039 per PR.
- **The rules baseline is still better on reviewers and issues:** reviewer@3 0.617 vs the
  student's 0.572, and issue@5 0.902 vs 0.883. The deployed reviewer rule (memory ranking
  first) raises the student's reviewer@3 on test from 0.572 to 0.612
  ([memory_ablation.json](results/memory_ablation.json) @ 6c88ef1).

### Untrusted input, on the test split

| Check | Result | Source |
|---|---|---|
| Prompt injection, 2,000 poisoned test PRs | PromptGuard 2 blocked 11.1%. End to end, 0.40% of attacks put their canary into a proposed action, all of them text echoed into the summary; no label or reviewer canary ever reached an action | [redteam_test.json](results/redteam_test.json) @ 6f7ab0d |
| False positives, 9,573 clean test PRs | 0.25% quarantined | [redteam_test.json](results/redteam_test.json) @ 6f7ab0d |
| Secrets, 600 synthetic in 12 formats | detect-secrets recall 93.5% | [secrets_synthetic.json](results/secrets_synthetic.json) @ 7d1f18f |
| Personal data, 6,807 Kaggle essays | Presidio F5 0.685 (recall 0.743) | [pii_kaggle.json](results/pii_kaggle.json) @ 110ec4e |

### Deployed on GCP

These runs replayed historical PRs through the deployed system as signed webhooks. The
worker served each PR from open-time data only, and nothing was written to GitHub. Costs
are measured usage priced at list prices, with the free tier not applied.

| Run | Result | Source |
|---|---|---|
| Pilot: 50 validation PRs, 3 at a time | 50 proposed; $0.028 per PR; p95 432 s from sending the webhook to seeing the stored triage | [pilot.json](results/pilot.json) @ bbcd83a |
| Burst: 500 PRs at once, plus 50 redeliveries | 0 duplicate proposals; all 50 redeliveries dropped; **0 lost**. The burst overloaded 10 workers: 168 finished, and the other 332 are all accounted for in the dead-letter queue | [burst.json](results/burst.json) @ 2b7ab61 |
| Shadow replay: test PRs, stopped at 103 (all numpy) | 93 proposed, 0 lost; $0.0246 per PR. Deployed vs offline GGUF on the same PRs: 57.7% vs 61.5% maintainer match (n=26), McNemar p=1.0 | [shadow.json](results/shadow.json) @ bd3590a; [shadow_replay_model.json](results/shadow_replay_model.json) @ 23b8d5d |

The shadow replay checks that the CPU deployment behaves like the offline GPU measurement.
It covers only numpy, the repo the model is weakest on, so the offline test numbers above
remain the quality figure.

## How it works

```mermaid
flowchart LR
  GH[GitHub: PR opened] -->|signed webhook| API[api<br/>signature check,<br/>delivery dedupe]
  API -->|publish| PS[(Pub/Sub topic)]
  PS -->|push, retries| W[worker]
  PS -.->|after N attempts| DLQ[(dead-letter queue<br/>+ alert)]
  W -->|read-only App| GHR[GitHub API]
  W --> SCR[screen: PromptGuard 2,<br/>Presidio, detect-secrets]
  SCR -->|quarantined| Q[stop: no model call]
  SCR --> M[model: student GGUF<br/>on llama.cpp, CPU]
  M --> V[validators, retries,<br/>fallback filter]
  V --> P[(proposal + content hash<br/>in Postgres)]
  P --> H{person approves<br/>with the hash}
  H --> X[executor<br/>writing App, allowlist]
  X -->|labels, reviewers, comment| GH
```

1. **The api** checks the webhook's HMAC signature, stores the delivery id (a repeated
   delivery is dropped), publishes the event and answers 202 at once.
2. **The worker** gathers the PR's context the same way the training prompts were built:
   diff, files, open-time candidate labels, reviewers and issues. It reads GitHub through a
   second, read-only App.
3. **Screening** runs before any model call. A PR that looks like an injection is
   quarantined. Personal data and secrets are redacted.
4. **The student** returns a typed `TriageOutput` through Pydantic AI. Repo rules (labels
   exist, reviewers eligible, issues open) are checked, and errors go back to the model up to
   2 times. A fallback filter keeps the valid parts of an output that still fails.
5. **A proposal** is stored with the SHA-256 of its content. Nothing has been written to
   GitHub yet.
6. **The executor** is the only component with write access. It runs an action only if a
   person approved that exact content hash, the action is on the allowlist, and it has not run
   before.

The design decisions, with the evidence behind them, are in [docs/adr/](docs/adr/):
0001–0006 cover the model and the pipeline, and 0007–0011 the deployment.

## Screenshots of the deployment

The GCP deployment was torn down after measuring. These screenshots were taken before the
teardown.

| | |
|---|---|
| ![Monitoring dashboard](docs/images/monitoring-dashboard.jpeg) | ![Langfuse traces](docs/images/langfuse.jpeg) |
| Cloud Monitoring dashboard | One Langfuse trace per PR, with a span per step |
| ![Dead-letter alert](docs/images/dead-letter-alert.jpeg) | ![API docs](docs/images/api-docs.jpeg) |
| The dead-letter alert, firing during the burst | The api's endpoints |

## Running it

- **Locally, end to end against a sandbox repo:** [docs/runbooks/sandbox-e2e.md](docs/runbooks/sandbox-e2e.md).
  It runs the student GGUF on llama.cpp (CPU), Postgres in Docker, the app in one process,
  and a tunnel for GitHub's webhook.
- **On GCP:** [docs/runbooks/gcp-deploy.md](docs/runbooks/gcp-deploy.md), with Terraform in
  [infra/terraform/](infra/terraform/). It covers deploying, replaying history, pausing and
  tearing everything down.
- **Tests:** they need Postgres.
  ```powershell
  docker compose up -d db
  $env:PRPILOT_TEST_DATABASE_URL = "postgresql+asyncpg://prpilot:prpilot@localhost:5433/prpilot_test"
  uv run pytest
  ```
  CI also runs ruff, gitleaks, a Promptfoo red-team suite against the screening step, and
  Terraform fmt and validate.

## Repository

| Path | What |
|---|---|
| `prpilot_core/` | The pipeline: context, screening, triage agent, fallback, memory, proposals, executor, GitHub clients |
| `services/` | `api`, `worker` and `executor` (Cloud Run), and `local.py` (everything in one process) |
| `prpilot_data/` | The dataset build from GH Archive and GitHub, prompt packs, red-team set and evaluations ([data card](data/README.md)) |
| `eval/` | The eval harness, a separate project that never imports app code |
| `training/` | Teacher labels, LoRA fine-tuning, baselines and the GGUF export (Slurm on Northeastern's Explorer cluster) |
| `replay/`, `loadtest/` | Replays through the deployed system, and the burst test |
| `infra/terraform/` | The GCP deployment |
| `results/` | Every recorded result |
| `PROGRESS.md` | The log of every task, with its results, commits and decisions |

## Tech stack

- **Python 3.12** with uv. Pydantic AI for the triage agent; FastAPI; SQLAlchemy and asyncpg
  on Postgres 17, with Alembic migrations.
- **Model:** Qwen3-1.7B with LoRA, trained on H200s; vLLM for the teacher and baselines;
  llama.cpp to serve the GGUF.
- **Screening:** PromptGuard 2, Presidio and detect-secrets.
- **GCP:** Cloud Run, Pub/Sub, Cloud SQL, Secret Manager, Artifact Registry and Cloud
  Monitoring, all in Terraform. Langfuse Cloud for traces.
- **CI:** GitHub Actions, with Workload Identity Federation for the Terraform plan while the
  deployment existed.

## Limitations

- **numpy, held out of training, is much weaker.** Maintainer match there is 42.2% for the
  GGUF, and it often writes numpy's numbered labels as invalid JSON. In the shadow replay, the
  fallback filter had to rescue 49 of 103 outputs.
- **Labels are the weakest field:** label F1 is 0.38 on test.
- **CPU serving costs about $0.025–0.029 per PR**, mostly spent on prefill of long diffs.
  That is 6–7 times Gemini 3.8 Flash's $0.0039 at similar accuracy. A GPU endpoint was not
  priced.
- **Under a burst larger than capacity, events go to the dead-letter queue, not to a late
  triage.** Pub/Sub counts each "no free worker" answer as a delivery attempt, and a PR that
  takes longer than the 10-minute ack deadline is delivered twice. Nothing is lost or
  duplicated, but the queue needs a redrive after an overload ([ADR 0007](docs/adr/0007-pubsub-push-between-api-and-worker.md)).
- **The shadow replay stopped at 103 PRs**, all from numpy, so there is no end-to-end figure
  across all four repos.
- **No approval web page:** approvals go through the API with an admin token.
- **Risk has no metric:** only 40 test PRs (0.42%) were reverted, too few to score.
