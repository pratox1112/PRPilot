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
| 0.3 | GitHub App webhook | Webhook arrives at test endpoint | Signed `pull_request/opened` verified at local endpoint via tunnel; unsigned → 401 | **Go** |
| 0.4 | Human-labeled PR volume | ≥3,000 PRs with a human-applied type label | ~25,756 estimated (95% CI 19,985–32,289) across 10 candidates | **Go** |
| 0.5 | Revert positives in test window | ≥50 | 45 positives in test set (rate 0.38%) | **No-go** → risk is a feature with no metric |
| 0.6 | Teacher agreement on type | ≥60% of 200 train PRs | 130/200 = 65.0% (95% CI 58.2–71.3%), thinking on | **Go** (CI lower bound below 60%) |
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

### 2026-10-26 — Task 0.5 — Revert positives (risk ground truth)
- Status: done (no-go; fallback chosen)
- What changed: `scripts/feasibility/revert_links.py` (revert → original linking, unit-tested), `scripts/feasibility/reverts.py`.
- Results: test-set positives = 45 (43 on default branch) out of 11,837 merged test-set PRs, positive rate 0.38%; go threshold 50 → no-go. Test set = 2025-07..2026-03 window of kubernetes, cilium, pandas + all of numpy (held out). 278 revert PRs found, 259 linked (explicit ref / commit SHA / title match), 19 unlinked, 1 revert-of-revert skipped → results/feasibility_reverts.json @ 5b69a1f77bd255df276b90e7aab678d2dec10c90 (clean tree; reverts searched until 2026-10-26).
- Dataset hash: positives `positives_hash` in the results file
- Notes / decisions / problems:
  - Owner decision (2026-10-26): risk stays in `TriageOutput` as a feature, but no risk metric (recall / AUROC) is reported anywhere. Resume may describe risk classification as a feature only.
  - Reason beyond the threshold: with a 0.38% base rate, ~50 test positives would give a recall CI of roughly ±13 points, and the train split has only ~150 positives.
  - Not chosen: widening the proxy to "fix PR within 14 days touching the same files" (noisy), or re-linking the 19 unlinked reverts after seeing the result (would look like tuning the measurement to pass).
  - Known blind spot of the method: reverts pushed as direct commits without a PR are not found.
  - Phase 2 impact: drop "Risk recall / AUROC" from the metric table (§5.3).
- Next: 0.6 (teacher), 0.3 (GitHub App)

### 2026-10-27 — Task 0.3 — GitHub App and webhook delivery
- Status: done
- What changed: `prpilot_core/github/webhooks.py` (X-Hub-Signature-256 HMAC verification, constant-time compare, unit-tested); `scripts/feasibility/webhook_receiver.py` (FastAPI test endpoint that rejects bad signatures, saves deliveries to `data/raw/webhooks/`, flags redeliveries by `X-GitHub-Delivery`).
- Results: signed `pull_request/opened` (PR #1, `pratox1112/prpilot-sandbox`) verified and accepted (202) → results/feasibility_github_app.json @ f3412121555ffe324816784f70ba95806c9880e1 (clean tree). Unsigned request → 401, both locally and through the tunnel (manual check).
- Dataset hash: n/a
- Notes / decisions / problems:
  - GitHub App `prpilot-dev-pratox1112`: repository permissions Contents read, Issues read/write, Metadata read, Pull requests read/write; subscribed to Pull request events; installable only on the owner's account; installed only on `prpilot-sandbox` (installation 175652280). Private key stored outside the repo (`~/.prpilot/github-app.pem`); App ID, key path and webhook secret in `.env`.
  - Local delivery used a Cloudflare quick tunnel (URL changes on every restart); the deployed api replaces it in Phase 6.
  - A PR opened before the App is installed sends no event; `ping` and `installation/created` arrived first.
- Next: 0.6 (teacher), then 0.9 review

### 2026-10-27 — Task 0.6 (part) — Teacher environment and smoke test on Explorer
- Status: done (smoke test only; agreement run next)
- What changed: teacher env on scratch built with uv (`training/slurm/setup_vllm_env.sh`), pinned in `training/teacher/requirements-vllm.lock.txt` (vllm 0.31.0+cu129, torch 2.13.0+cu129, transformers 5.17.0; torchcodec removed); `training/slurm/env.sh` (spawn workers, caches on scratch, cuda-compat on the library path); smoke job `training/slurm/vllm_smoke.sbatch`.
- Results: teacher `Qwen/Qwen3.5-122B-A10B-GPTQ-Int4` @ 30cd92c loaded on 1x H200 (67.7 GiB weights, 53.7 GiB KV cache, load + compile 334 s). Synthetic smoke PRs (N = 3, same as 0.8): first-attempt valid JSON 3/3 with thinking off and 3/3 with thinking on, repo rules 3/3, types bug/docs/dependency as expected; thinking on uses 540–769 output tokens, no truncation → results/feasibility_vllm_smoke.json (Slurm job 11411689, code @ 46a69b4daa49c61cdc85c7b68ff3c7bf0223d915, clean). Smoke check only, not the agreement metric.
- Dataset hash: n/a (synthetic samples recorded in the results file)
- Notes / decisions / problems (each found by the smoke test, in order):
  1. Home dir full (77 of 75 GB) → env, caches and code clone moved to scratch; only results stay in `~/prpilot-keep`.
  2. pip picked up old packages from `~/.local` → `PYTHONNOUSERSITE=1`; contaminated lock file rejected.
  3. vLLM forked its engine after CUDA was initialised → `VLLM_WORKER_MULTIPROC_METHOD=spawn`.
  4. H200 nodes run driver 570.86 (CUDA 12.8); default torch/vLLM builds are CUDA 13 → switched to the cu129 builds. Owner: a driver update will not happen.
  5. torchcodec exists only as a CUDA 13 build → removed (vLLM treats it as optional; we run text-only).
  6. vLLM's GPTQ Marlin kernel ships PTX built with CUDA 12.9, which driver 570 cannot JIT (`cudaErrorUnsupportedPtxVersion`) → NVIDIA forward compatibility via conda-forge `cuda-compat` 13.0.2 + `cuda-compat-impl` 580.95.05 in the env; effective driver API 13000, Marlin runs.
  7. vLLM's compile cache defaulted to the full home dir → all tool caches moved to scratch.
  - For Phase 3 training (no vLLM), plan a separate env on torch 2.13.0+cu126, which driver 570 supports natively.
- Next: 0.6 agreement run (200 train-window PRs)

### 2026-10-27 — Task 0.6 — Teacher agreement with maintainers
- Status: done
- What changed: `scripts/feasibility/agreement_sample.py` (200-PR sample), `training/teacher/teacher_agreement.py` + `training/slurm/teacher_agreement.sbatch` (teacher run on 1x H200), `scripts/feasibility/score_agreement.py` (scoring); ADR 0003 (teacher choice).
- Results: teacher type == maintainer primary type, first attempt, invalid reply = disagreement (model metric): thinking on 130/200 = 65.0% (95% CI [0.582, 0.713]), valid JSON 200/200; thinking off 125/200 = 62.5% (95% CI [0.556, 0.689]), valid JSON 197/200. By repo (thinking on): kubernetes 0.587, cilium 0.61, pandas 0.758 → results/feasibility_teacher_agreement.json @ 5259fb3e60d3e3c31ae4a821bbafb82654b2dd71 (clean tree; teacher Slurm job 11412144 at the same commit). Sample record → results/feasibility_teacher_sample.json @ 8fffe7b49d6de55ab62ed67c37dfb0a73fb7c7cf.
- Dataset hash: inputs `inputs_hash` in both results files (must match; the scorer checks it)
- Notes / decisions / problems:
  - Sample: 200 drawn at random (seed 0) from 407 train-window PRs with a human type label (kubernetes 75, pandas 66, cilium 59). Held-out numpy excluded. Approximations: final (not open-time) title/body/diff; slash-command lines stripped from bodies (71 of 200 had them); diffs truncated to 24,000 chars (17 PRs); 3 diffs too large for the API (file list only).
  - Per type (thinking on): docs 30/30, bug 30/34, ci/build 20/23, test 12/17, refactor 28/48, feature 8/16 (6 called dependency), other 2/31, dependency 0/1.
  - `other` (mostly cilium `release-note/misc`) drives the disagreement: excluding those 31 PRs, agreement is 128/169 = 75.7% (computed from the same results file; informal). Revisit the `release-note/misc` -> other mapping in Phase 1.
  - Point estimate passes 60%, but the 95% CI lower bound (58.2%) does not; recorded as a go with that caveat.
  - Thinking on chosen: +5 PRs (not statistically meaningful) and 200/200 valid JSON; cost 146 s vs 47.7 s for 200 PRs on one H200 (load + compile 249 s).
  - This was the train window, so choosing the mode on it does not touch the test set (hard rule 4).
- Next: 0.9 Phase 0 review

