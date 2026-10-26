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
| 0.1 | Explorer GPU job | Job runs on a GPU | Owner-confirmed done; H200/A100 available, 4 GPUs/user | **Go** |
| 0.2 | GCP project + budget alert | Budget alert active | Owner-confirmed done | **Go** |
| 0.3 | GitHub App webhook | Webhook arrives at test endpoint | [TBD] | [TBD] |
| 0.4 | Human-labeled PR volume | ≥3,000 PRs with a human-applied type label | ~25,756 estimated (95% CI 19,985–32,289) across 10 candidates | **Go** |
| 0.5 | Revert positives in test window | ≥50 | [TBD] | [TBD] |
| 0.6 | Teacher agreement on type | ≥60% of 200 train PRs | [TBD] | [TBD] |
| 0.7 | LlamaFirewall + Presidio | Both run on Python 3.12 | LlamaFirewall 1.0.3 breaks on import; PromptGuard 2 direct (fallback) + Presidio run | **Go** (via fallback) |
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
- Status: done (owner-confirmed 2026-10-26; `nvidia-smi` output and account check not recorded here)
- What changed: account active; partitions and limits recorded from `sinfo` / `sacctmgr`.
- Results: `gpu` partition (8 h limit) has H200 x8 (4 nodes), A100 x3–4 (3 nodes), V100, T4; `gpu-short` / `gpu-interactive` 2 h. QOS `gpu`: max 4 GPUs and 4 jobs per user. Storage: home 75 GB; scratch on VAST, purged monthly, no backup.
- Dataset hash: n/a
- Notes / decisions / problems: Slurm accounts are course accounts (`eece7268.*`, `eece7398.*`); confirm they may be used for this project. `nvidia-smi` test job output (A100 memory size, driver version) still to record.
- Next: GPU test job output, account check

### 2026-10-24 — Task 0.2 — GCP project and budget alert
- Status: done (owner-confirmed 2026-10-26; budget amount and credit details not recorded here)
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

### 2026-10-26 — Task 0.4 — Human-labeled PR volume
- Status: done
- What changed: label attribution (`scripts/feasibility/label_attribution.py`, bot / slash-command rules of §4.3, unit-tested), cached GraphQL client, `label_volume.py` (fetch + estimate), `data/config/repos.toml`, `data/config/type_mappings.toml`.
- Results: estimated usable PRs (≥1 human-applied type label, merged 2023-01-01..2026-03-31) = 25,756, 95% CI [19,985, 32,289]; sample = 50 merged PRs per repo per window x 4 windows x 10 repos (N = 2,000) → results/feasibility_label_volume.json @ 43bf02106e490db306fe7694fab4c6643675444e (clean tree). Pass-1 attribution report → results/feasibility_label_volume_pass1.json @ 9c8e1c4b45bce880909714c79c3b5e2e6ba97a3f.
- Dataset hash: sample `sha256:2b34e108cf61fbc8a75f0dc5dc1da2b246d6ef4eb4ba781df959757cb75450f1`, mappings `sha256:0579caac6375cc7dcfa6d5fba5532a878f618e9c4e3a22a0cc91d98e02122e2a`
- Notes / decisions / problems:
  - Per repo: kubernetes 8,595, cilium 8,397, pandas 4,299, numpy 2,614, matplotlib 1,204; the other five ≤ 229 each.
  - Selected (in `repos.toml`): kubernetes, cilium, pandas (split by time); numpy held out as the test-only repo. Dropped: matplotlib (PSF-based license, not MIT/Apache/BSD), scikit-learn / prometheus / containerd (type labels mostly bot-applied or absent), electron / angular (no PR type labels).
  - Kubernetes `/kind` slash-command attribution works (kind/* credited to the human who wrote the command).
  - Types are imbalanced: cilium dominated by `other` (`release-note/misc`), kubernetes by `refactor`, `feature` rare everywhere; ~70 of 2,000 sampled PRs carry more than one type. Primary-type rule added to `type_mappings.toml` (bug > feature > dependency > ci/build > test > docs > refactor > other).
  - Informal check from cache (not saved as a result): excluding PRs whose only type is `other` still leaves ~21k.
  - Requirement for the eval harness: report maintainer match per type and per repo, not only overall, so a majority-class model cannot look good.
  - Sample is the most recently created merged PRs inside each window, not uniform.
- Next: 0.5

### 2026-10-26 — Task 0.7 — Injection and PII screening tools
- Status: done (via the documented fallback)
- What changed: `prpilot_core/screening/prompt_guard.py` (PromptGuard 2 at pinned revisions, overlapping 512-token windows, max score, threshold 0.9); ADR 0002; `scripts/feasibility/screening_check.py`; Presidio + spaCy `en_core_web_lg==3.8.0` in the `screening` group.
- Results: smoke check, N = 10 synthetic texts (5 injected, 5 clean): PromptGuard 2 86M blocked 3/5 injected, 0/5 clean; 22M blocked 0/5 injected, 0/5 clean at threshold 0.9 → results/feasibility_screening.json @ d44473d03d5b4f1da485e3e7cac7782e03b8d2d0 (clean tree). Not the §7.2 red-team metric.
- Dataset hash: see `dataset.hash` in the results file
- Notes / decisions / problems:
  - `llamafirewall==1.0.3` fails on import with current `huggingface_hub` (no `HfFolder`), truncates at 512 tokens, and does not pin the model → replaced by PromptGuard 2 directly (ADR 0002). Resume wording becomes "PromptGuard 2".
  - Windowing matters: payload after a long diff scored 0.996 windowed vs 0.003 when truncated at 512 tokens.
  - 86M missed both task-specific payloads ("note to the triage bot…", instruction in a code comment). Expected weakness of PromptGuard on indirect injections; this is why the approval lock and the §7.2 LLM-classifier comparison matter.
  - A clean text containing "ignore … instructions" scored 0.638 (below 0.9): false-positive risk to watch when tuning the threshold on validation.
  - 22M scored every sample < 0.04; at 0.9 it blocks nothing here. Needs its own threshold if used.
  - Presidio corrupts code: in a diff, `cfg.scale` was detected as a URL and redacted to `<URL>ale`, and identifiers were tagged PERSON/LOCATION. Plan for Phase 4: run Presidio on prose fields (title, body, comments) only; use the secret scanner for diffs.
  - CPU cost: 86M took ~14 s for a 12-window diff (~1.2 s per window); 22M ~5 s. Relevant for worker sizing.
- Next: 0.5
