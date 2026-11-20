# PRPilot: pull request triage with a fine-tuned 1.7B model, from webhook to approved write

[![CI](https://github.com/pratox1112/PRPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/pratox1112/PRPilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

<p>
<img alt="Python" src="https://img.shields.io/badge/Python_3.12-3776AB?style=for-the-badge&logo=python&logoColor=white">
<img alt="PyTorch" src="https://img.shields.io/badge/PyTorch-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white">
<img alt="Hugging Face" src="https://img.shields.io/badge/Hugging_Face-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black">
<img alt="vLLM" src="https://img.shields.io/badge/vLLM-30A2FF?style=for-the-badge">
<img alt="llama.cpp" src="https://img.shields.io/badge/llama.cpp_GGUF-1F2937?style=for-the-badge">
<img alt="Pydantic AI" src="https://img.shields.io/badge/Pydantic_AI-E92063?style=for-the-badge&logo=pydantic&logoColor=white">
<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white">
<img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL_17-4169E1?style=for-the-badge&logo=postgresql&logoColor=white">
<img alt="SQLAlchemy" src="https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white">
<br>
<img alt="Google Cloud" src="https://img.shields.io/badge/Google_Cloud-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white">
<img alt="Cloud Run" src="https://img.shields.io/badge/Cloud_Run-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white">
<img alt="Pub/Sub" src="https://img.shields.io/badge/Pub%2FSub-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white">
<img alt="Terraform" src="https://img.shields.io/badge/Terraform-844FBA?style=for-the-badge&logo=terraform&logoColor=white">
<img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white">
<img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white">
<img alt="pytest" src="https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white">
<img alt="OpenTelemetry" src="https://img.shields.io/badge/OpenTelemetry-000000?style=for-the-badge&logo=opentelemetry&logoColor=white">
<img alt="Langfuse" src="https://img.shields.io/badge/Langfuse-0A0A0A?style=for-the-badge">
<img alt="Slurm" src="https://img.shields.io/badge/Slurm_(H200)-2E7D32?style=for-the-badge">
</p>

PRPilot is a GitHub App that triages new pull requests. When a PR opens, it proposes the
PR's type, labels, reviewers and related issues, with a short summary, rationale and risk.
It writes to GitHub only after a person approves that exact proposal. Untrusted PR text is
screened for prompt injection, personal data and secrets before any model sees it.

The model is Qwen3-1.7B, fine-tuned with LoRA on labels from a 122B teacher, and served as a
1.05 GiB Q4_K_M GGUF on CPU. It is scored against what maintainers actually did on 9,573
held-out PRs from cilium, kubernetes and pandas, plus numpy, a repo it never saw in training.

## Highlights

| | |
|---|---|
| **Maintainer match** | **70.3%** on 5,562 test PRs, ahead of its 122B teacher (65.0%, p<1e-6) at about 1/20 of its GPU time |
| **As deployed** (Q4_K_M GGUF, with retries) | 69.7% on the same test PRs, and tied with Gemini 3.8 Flash on a 500-PR sample (68.1% vs 68.1%, p=1.0) |
| **Prompt injection** | 0.40% of 2,000 attacks reached a proposed action; no label or reviewer canary ever did. 0.25% of clean PRs were quarantined |
| **Burst test** | 500 webhooks at once plus 50 redeliveries: **0 duplicate proposals, 0 lost events** |
| **Shadow replay** | The cloud CPU deployment matches the offline GPU measurement on the same 103 test PRs (McNemar p=1.0) |
| **Serving cost** | $0.025–0.029 per PR on Cloud Run CPU, from measured usage at list prices |
| **Writes** | Only after approval of the content hash, only for allowlisted actions, and never twice |

## Architecture

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
2. **The worker** gathers the PR's context with the same code that built the training
   prompts: diff, files, open-time candidate labels, reviewers and issues. It reads GitHub
   through a second, read-only App.
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

The prompt the deployed worker builds is byte-identical to the offline evaluation prompt
(checked on 500 validation PRs), so the offline numbers describe the deployed model.

## Results

Every number links to the file it comes from, recorded on a clean tree at the commit shown.
Maintainer match means the predicted PR type equals the type the maintainers labelled.

### Model (test split, frozen before training, scored once per system)

| System | Maintainer match (n=5,562) | Notes | Source |
|---|---|---|---|
| **Student** (Qwen3-1.7B + LoRA, bf16) | **70.3%** (CI 69.1–71.5) | valid JSON 98.7% on the first attempt, unconstrained; 0.048 GPU-s per PR | [student_test.json](results/student_test.json) @ 0cf4899 |
| Student as deployed (Q4_K_M GGUF, with retries) | 69.7% (CI 68.5–70.9) | 1.05 GiB, served on CPU | [student_gguf_test.json](results/student_gguf_test.json) @ 3baac29 |
| Teacher (Qwen3.5-122B-A10B, GPTQ-Int4) | 65.0% (CI 63.8–66.3) | 0.951 GPU-s per PR | [student_test.json](results/student_test.json) @ 0cf4899 |
| Rules baseline | 53.0% | | [student_test.json](results/student_test.json) @ 0cf4899 |
| Untuned Qwen3-1.7B | 36.1% | | [student_test.json](results/student_test.json) @ 0cf4899 |

- **Against a frontier model:** on a seeded 500-PR test sample (n=279 typed), the student
  with retries and Gemini 3.8 Flash with retries both reach 68.1% (19 vs 19, p=1.0). Gemini
  cost $0.0039 per PR.
- **Reviewers:** the deployed reviewer rule (memory ranking first, then the model) raises
  reviewer@3 on test from 0.572 to 0.612
  ([memory_ablation.json](results/memory_ablation.json) @ 6c88ef1).

### Untrusted input (test split)

| Check | Result | Source |
|---|---|---|
| Prompt injection, 2,000 poisoned test PRs | PromptGuard 2 blocked 11.1%. End to end, 0.40% put their canary into a proposed action, all of them text echoed into the summary; no label or reviewer canary reached an action | [redteam_test.json](results/redteam_test.json) @ 6f7ab0d |
| False positives, 9,573 clean test PRs | 0.25% quarantined | [redteam_test.json](results/redteam_test.json) @ 6f7ab0d |
| Secrets, 600 synthetic in 12 formats | detect-secrets recall 93.5% | [secrets_synthetic.json](results/secrets_synthetic.json) @ 7d1f18f |
| Personal data, 6,807 Kaggle essays | Presidio F5 0.685 (recall 0.743) | [pii_kaggle.json](results/pii_kaggle.json) @ 110ec4e |

### Deployed on GCP (Cloud Run, Pub/Sub, Cloud SQL)

These runs replayed historical PRs through the deployed system as signed webhooks. The
worker served each PR from open-time data only, and nothing was written to GitHub. Costs are
measured usage priced at list prices, with the free tier not applied.

| Run | Result | Source |
|---|---|---|
| Pilot: 50 validation PRs, 3 at a time | 50 proposed; $0.028 per PR; p95 432 s from sending the webhook to seeing the stored triage | [pilot.json](results/pilot.json) @ bbcd83a |
| Burst: 500 PRs at once, plus 50 redeliveries | 0 duplicate proposals; all 50 redeliveries dropped; **0 lost**: every event was either triaged or held in the dead-letter queue | [burst.json](results/burst.json) @ 2b7ab61 |
| Shadow replay: 103 numpy test PRs | 93 proposed, 0 lost; $0.0246 per PR. Deployed vs offline GGUF on the same PRs: no significant difference in maintainer match (McNemar p=1.0) | [shadow.json](results/shadow.json) @ bd3590a; [shadow_replay_model.json](results/shadow_replay_model.json) @ 23b8d5d |

The shadow replay checks that the CPU deployment behaves like the offline GPU measurement.

| | |
|---|---|
| ![Monitoring dashboard](docs/images/monitoring-dashboard.jpeg) | ![Langfuse traces](docs/images/langfuse.jpeg) |
| Cloud Monitoring dashboard | One Langfuse trace per PR, with a span per step |
| ![Dead-letter alert](docs/images/dead-letter-alert.jpeg) | ![API docs](docs/images/api-docs.jpeg) |
| The dead-letter alert, firing during the burst | The api's endpoints |

## Repository layout

```
prpilot_core/      the pipeline: context, screening, triage agent, fallback, memory,
                   proposals, executor, GitHub clients, Pub/Sub queue, tracing
services/          api, worker and executor (Cloud Run); local.py runs everything in one process
prpilot_data/      dataset build (GH Archive + GitHub), prompt packs, red-team set, evaluations
eval/              eval harness, a separate project that never imports app code
training/          teacher labels, LoRA fine-tuning, baselines, GGUF export (Slurm jobs)
replay/            replays of historical PRs through the deployed system, shadow replay scoring
loadtest/          burst test with redeliveries
redteam/           Promptfoo suite run against the screening step in CI
infra/terraform/   the GCP deployment, one module per concern
migrations/        Alembic migrations for Postgres
docker/            images for app, worker and model
results/           every recorded result (JSON, with commit and inputs' hashes)
docs/              ADRs, runbooks, screenshots
data/README.md     the data card
PROGRESS.md        the log of every task, with results, commits and decisions
```

## How to run it

**Set up and test** (Python 3.12 with uv, Docker for Postgres):
```powershell
uv sync
docker compose up -d db
$env:PRPILOT_TEST_DATABASE_URL = "postgresql+asyncpg://prpilot:prpilot@localhost:5433/prpilot_test"
uv run pytest
```
CI also runs ruff, gitleaks, the Promptfoo red-team suite against the screening step, and
Terraform fmt and validate.

**Run the bot locally against a sandbox repo:** the student GGUF on llama.cpp (CPU),
Postgres in Docker, the app in one process, and a tunnel for GitHub's webhook. Follow
[docs/runbooks/sandbox-e2e.md](docs/runbooks/sandbox-e2e.md).

**Build the data, train and evaluate:**
- The dataset is built with `uv run --group data python -m prpilot_data ...`; the steps are in
  the [data card](data/README.md).
- Teacher labelling, fine-tuning, the baselines and the GGUF export run as Slurm jobs on
  H200s, from [training/](training/).
- Scoring uses the harness: `uv run --project eval python -m prpilot_eval score ...`.

**Deploy to GCP, replay history and tear down:** Terraform in
[infra/terraform/](infra/terraform/), with every step in
[docs/runbooks/gcp-deploy.md](docs/runbooks/gcp-deploy.md). That covers the deploy, the pilot,
the burst and the shadow replay, pausing, and teardown to $0.

## Data and licence

- **PR data** comes from public repositories (cilium, kubernetes, pandas and numpy), read
  through the GitHub API and GH Archive. Only what existed when each PR was opened is used as
  input. The data itself is not committed; the [data card](data/README.md) describes how it
  is built.
- **Personal-data evaluation** uses the Kaggle "PII Data Detection" essays, downloaded by the
  owner under the competition's terms and not committed.
- **Models:** Qwen3-1.7B and Qwen3.5-122B-A10B are Apache-2.0. PromptGuard 2 is Meta's,
  under its own licence, and gated on Hugging Face.
- **Code:** MIT ([LICENSE](LICENSE)). The licence covers this repository's code, not the data or the models above.

---

Design decisions and the evidence behind them are in [docs/adr/](docs/adr/). The full task
log is in [PROGRESS.md](PROGRESS.md).
