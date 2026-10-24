# PRPilot — Progress

## Resume Numbers

Every value comes from a saved file under `results/` at the listed commit. `[TBD]` means not measured yet.

| Placeholder | Value | Results file | Commit |
|---|---|---|---|
| Maintainer match [X]% on [N] PRs | [TBD] | | |
| Student size | [TBD] | | |
| Teacher model | [TBD] | | |
| Teacher-relative accuracy [X]% | [TBD] | | |
| JSON validity [X]% (model, first attempt, unconstrained) | [TBD] | | |
| Valid output with retries [X]% (system) | [TBD] | | |
| Frontier comparison model | [TBD] | | |
| Cost ratio [X]x vs frontier | [TBD] | | |
| Shadow replay: [N] PRs, end-to-end maintainer match [X]% | [TBD] | | |
| Injection block rate [X]% of [N] | [TBD] | | |
| False positives [X]% | [TBD] | | |
| PII F5 | [TBD] | | |
| Burst size [N], p95 [X]s, duplicates | [TBD] | | |

## Feasibility go/no-go

| # | Check | Go if | Result | Decision |
|---|---|---|---|---|
| 0.1 | Explorer GPU job | Job runs on a GPU | [TBD] | [TBD] |
| 0.2 | GCP project + budget alert | Budget alert active | [TBD] | [TBD] |
| 0.3 | GitHub App webhook | Webhook arrives at test endpoint | [TBD] | [TBD] |
| 0.4 | Human-labeled PR volume | ≥3,000 PRs with a human-applied type label | [TBD] | [TBD] |
| 0.5 | Revert positives in test window | ≥50 | [TBD] | [TBD] |
| 0.6 | Teacher agreement on type | ≥60% of 200 train PRs | [TBD] | [TBD] |
| 0.7 | LlamaFirewall + Presidio | Both run on Python 3.12 | [TBD] | [TBD] |
| 0.8 | Pydantic AI + llama.cpp + Langfuse | Validated `TriageOutput` and a trace in Langfuse | 3/3 valid first attempt (N = 3 synthetic PRs); 3 traces visible in Langfuse | **Go** |

## Log

### 2026-10-24 — Task 0.0 — Repo scaffolding and CI
- Status: done
- What changed: uv project (Python 3.12, exact pins in `pyproject.toml` + `uv.lock`), `TriageOutput` static schema with unit tests, CI workflow (ruff, pytest, gitleaks), `.env.example`.
- Results: n/a
- Dataset hash: n/a
- Notes / decisions / problems: `main` protected by ruleset (PR required, merge commits only). CI becomes a required check after its first run. Models chosen (to be confirmed by 0.6 / 0.8):
  - Teacher: `Qwen/Qwen3.5-122B-A10B-GPTQ-Int4` @ `30cd92cba9707a9aba09d1e490ed4b66b78e9606` (Apache-2.0), 1x H200 with vLLM. Fallback: `Qwen/Qwen3.8-27B-FP8` @ `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`. Gemini only after a terms ADR.
  - Student base: `Qwen/Qwen3-1.7B` @ `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` (Apache-2.0).
  - Untrained student for serving checks: `Qwen/Qwen3-1.7B-GGUF` @ `90862c4b9d2787eaed51d12237eafdfe7c5f6077`, `Qwen3-1.7B-Q8_0.gguf`.
  - llama.cpp server image: `ghcr.io/ggml-org/llama.cpp@sha256:20c4196fc08b459a34ef78aaa05b101333b2fac687e30ec99f37b3c9dc071b44`.
- Next: 0.7, 0.8

### 2026-10-24 — Task 0.1 — Explorer account and GPU test
- Status: partial
- What changed: account active; partitions and limits recorded from `sinfo` / `sacctmgr`.
- Results: `gpu` partition (8 h limit) has H200 x8 (4 nodes), A100 x3–4 (3 nodes), V100, T4; `gpu-short` / `gpu-interactive` 2 h. QOS `gpu`: max 4 GPUs and 4 jobs per user. Storage: home 75 GB; scratch on VAST, purged monthly, no backup.
- Dataset hash: n/a
- Notes / decisions / problems: Slurm accounts are course accounts (`eece7268.*`, `eece7398.*`); confirm they may be used for this project. `nvidia-smi` test job output (A100 memory size, driver version) still to record.
- Next: GPU test job output, account check

### 2026-10-24 — Task 0.2 — GCP project and budget alert
- Status: partial
- What changed: owner reports project and budget alert are done.
- Results: budget amount, credit type and expiry: [TBD]
- Dataset hash: n/a
- Notes / decisions / problems: no paid resources created yet.
- Next: record budget and credit details

### 2026-10-24 — Task 0.8 — Pydantic AI + llama.cpp + Langfuse feasibility
- Status: done
- What changed: triage agent (`prpilot_core/agents/triage.py`) using `TextOutput` with our own JSON parsing and repo-rule retries; feasibility script `scripts/feasibility/pydantic_ai_check.py`; ADR 0001 (output mode).
- Results: first-attempt valid, static schema (model) = 3/3; first attempt also passing repo rules = 3/3; valid after retries (system, retry limit 2) = 3/3; N = 3 synthetic PRs → results/feasibility_pydantic_ai.json @ b36d622247a09e4f68c0671910f8a705e3a5be72 (clean tree)
- Dataset hash: see `dataset.hash` in the results file (synthetic samples)
- Notes / decisions / problems: Qwen3-1.7B-Q8_0 on llama.cpp `b11854-8e5a58df8` (pinned image), `--jinja` + `enable_thinking: false` gives clean JSON with no `<think>` block. Traces confirmed in Langfuse (US) by owner. Smoke check only, not a quality measure: the untrained model picked wrong labels / issue links on the synthetic PRs. Laptop CPU took ~7–9 s per ~640-token prompt, so long diffs will be slow; relevant for Cloud Run sizing.
- Next: 0.4 (data volume), 0.5 (revert counts)
