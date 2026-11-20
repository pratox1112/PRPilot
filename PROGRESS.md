# PRPilot — Progress

## Resume Numbers

Every value comes from a saved file under `results/` at the listed commit. `[TBD]` means not measured yet.

| Placeholder | Value | Results file | Commit |
|---|---|---|---|
| Maintainer match [X]% on [N] PRs | 70.3% on 5,562 test PRs (bf16 student); deployed Q4_K_M with retries 69.7% | results/student_test.json; results/student_gguf_test.json | 0cf4899; 3baac29 |
| Student size | Qwen3-1.7B + LoRA r16 (17.4M trainable, 1.0%); deployed as Q4_K_M GGUF, 1.05 GiB | results/student_gguf_test.json | 3baac29 |
| Teacher model | Qwen3.5-122B-A10B (GPTQ-Int4) @ `30cd92cb`; maintainer match 62.8% on 2,435 val PRs | results/baselines_val.json | 1b732c8 |
| Teacher-relative accuracy [X]% | +5.3 points over the teacher on test (70.3% vs 65.0%, p<1e-6) at ~1/20 of its GPU time (0.048 vs 0.951 GPU-s per PR) | results/student_test.json | 0cf4899 |
| JSON validity [X]% (model, first attempt, unconstrained) | 98.7% bf16; 90.4% Q4_K_M (test) | results/student_test.json; results/student_gguf_test.json | 0cf4899; 3baac29 |
| Valid output with retries [X]% (system) | 87.2% bf16; 75.9% Q4_K_M (test; untuned Qwen3-1.7B 60.4%) | results/student_test.json; results/student_gguf_test.json | 0cf4899; 3baac29 |
| Frontier comparison model | Gemini 3.8 Flash on Vertex AI (500-PR val sample; $0.0039 per PR raw, $0.0047 with retries) | results/baselines_val.json | 1b732c8 |
| Cost ratio [X]x vs frontier | [TBD] | | |
| Shadow replay: [N] PRs, end-to-end maintainer match [X]% | [TBD] | | |
| Injection block rate [X]% of [N] | PromptGuard 2 blocked 11.1% of 2,000 injected test PRs; end to end, 0.40% of them reached a proposed action (no label or reviewer canary did) | results/redteam_test.json | 6f7ab0d |
| False positives [X]% | 0.25% of 9,573 clean test PRs quarantined | results/redteam_test.json | 6f7ab0d |
| PII F5 | 0.685 on 6,807 Kaggle essays (recall 0.743) | results/pii_kaggle.json | 110ec4e |
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

## Phase 1 — Data pipeline

### 2026-10-28 — Task 1.1 — Data package
- Status: done
- What changed: `prpilot_data/` package (GitHub client, label attribution, input preparation and record helpers moved from `scripts/feasibility/` unchanged; Phase 0 scripts import them from the package). CLI `python -m prpilot_data`.
- Results: n/a
- Dataset hash: n/a
- Notes / decisions / problems: Phase 1 guidelines approved 2026-10-27 with all eight recommended choices (merged default-branch PRs only; drop cilium `release-note/misc` from the type mapping; keep bot-authored PRs with a flag; report issue recall with/without explicit `Fixes #N`; GH Archive after cost estimate; 6,000-token diff budget with the student tokenizer; `prpilot_data/` package).
- Next: 1.2

### 2026-10-28 — Task 1.2 — Open-time inputs: GH Archive check and GraphQL reconstruction
- Status: done
- What changed: `prpilot_data/gharchive.py` (dry-run-first BigQuery queries with a byte cap), `prpilot_data/open_time.py` (title from the earliest `RenamedTitleEvent.previousTitle`; body from the oldest edit-history entry; head commit = last commit before opening, or the commit before the first force push), `prpilot_data/validate_open_time.py`.
- Results:
  - GH Archive payload completeness: `opened` events carry title/body/author until GitHub trimmed event payloads on 2025-10-07; head and base SHA are present throughout → results/data_gharchive_sample.json @ a22eb4f5799055e97b746ce30ea694d42731194b, results/data_gharchive_validation.json @ 0f721931c5ee9efc8001744c6a1999a3667a209d (16 days, 554 opened PR events; 179.5 GiB billed, inside the free tier).
  - GraphQL reconstruction vs GH Archive truth (N = 554; title/body N = 475 before the trim), 0 s clock tolerance: title 475/475 = 100% (naive final value 82.5%); body 475/475 = 100% (naive 64.0%); head SHA 462/554 = 83.4% (naive 48.6%) → results/data_open_time_validation.json @ b42ff988ec000d4fca0d7df4aad679be364064bb (clean tree).
  - Open-time head commit still fetchable by SHA: not force-pushed 334/335; force-pushed 154/219 (70%) (same file).
- Dataset hash: validation sample `truth_hash` in the results file
- Notes / decisions / problems:
  - The body rule first used the oldest `createdAt`, which matched only 42/72: GitHub stores the original and the first edit with the same timestamp. The original is the last entry in the API's (newest-first) order; fixed and re-validated.
  - Head-SHA misses: 81 force-pushed PRs whose earlier commits GraphQL returns as null (flagged `unresolved`, final-diff fallback), 13 silently wrong guesses (about 2.3%).
  - 0 s tolerance beats 5 min overall (462 vs 457 correct).
  - Owner decision (option B): GraphQL reconstruction for all PRs, plus GH Archive head SHAs for the post-trim months 2025-10..2026-03 (est. 634 GiB, within the free tier). Unresolved heads are flagged and metrics are reported for clean vs flagged PRs. A full GH Archive query (~13.3 TiB, ~$77) was not run; it would raise correct open-time diffs from about 83% to about 88%, limited by commits that no longer exist.
- Next: 1.3 (stage A discovery at full scale)


### 2026-10-29 — Task 1.3 — Stages A–C at full scale: PRs, history, open-time diffs
- Status: done
- What changed: `prpilot_data/discover.py` (stage A, exact base-branch filter), `timeline.py` and `complete.py` (stage B, GraphQL history with every capped connection refetched page by page), `gharchive.py` post-trim query (stage A′), `diffs.py` and `tokens.py` (stage C: open-time `compare/<base at open>...<head at open>`, cut to 6,000 Qwen3-1.7B tokens); `require_clean_tree()` before every recorded run, REST rate-limit handling.
- Results:
  - Stage A: 28,845 merged default-branch PRs opened 2023-01-01..2026-03-31 (cilium 9,397, kubernetes 9,263, pandas 6,582, numpy 3,603) → results/data_discover.json @ 1c1a675a692d94b0e9608c8fa89a8a4934a333ef.
  - Stage A′: 2,484 GH Archive `opened` events with head SHA, 634.3 GiB billed (free tier) → results/data_gharchive_post_trim.json @ 65a3c54321911fdaee01549dbadb0a43422e79dd.
  - Stage B → results/data_timeline.json @ 2e30e0d128cf469acdb81c5db971ed81fb322342, results/data_timeline_complete.json @ 7a3b0f199f21b799e91935d76dfb6320c6b61439.
  - Stage C check vs GH Archive (post-trim PRs): heads 1,244/1,466 agree; bases 197/200 = 98.5% give a byte-identical diff → results/data_diffs_validation.json @ 57d96d8c1ec70db8de781e83ffa9a4b35582a765.
  - Stage C (default-branch PRs): open-time diff 25,206, final-diff fallback 4,440, unavailable 34 (rows include the 835 numpy backports dropped later; the dataset counts below are after that filter) → results/data_diffs.json @ 4c4a6ce23b64714ef8f860983706f84ce3b09d91.
- Dataset hash: per-repo input hashes in results/data_dataset.json `sources`
- Notes / decisions / problems:
  - First diff trial used the final head as fallback: PRs rebased before merging produced 10–60 MB diffs. Fallback changed to the PR's final diff, flagged.
  - GitHub search's `base:main` also matches `maintenance/*`; discovery had included 835 numpy backport PRs (their inflated diffs gave it away). Fixed with an exact `baseRefName` check and re-run.
  - The first stage A′ record was `dirty: true` (an uncommitted results file at run time). Re-run on a clean tree on 2026-11-02 after the free tier reset: the same 2,484 rows (order differs; no `ORDER BY`), so nothing downstream changed.
  - 2023 heads are often unresolved (GitHub no longer returns commits from before old force pushes); a GH Archive query for 2023 (~$20–25) was not run.
- Next: 1.4

### 2026-10-29 — Task 1.4 — Ground truth from maintainer actions
- Status: done
- What changed: `prpilot_data/ground_truth.py` (stage D): human labels and type (bot filter, kubernetes slash-command attribution, removed labels dropped); reviewers = reviewed before merge or requested by a person, excluding bot requests without a human `/cc`, CODEOWNERS (author-actor requests within 2 min of opening or of ready-for-review) and team requests; closing issues with their repo and whether named at open. Cilium `release-note/misc` removed from the type mapping (decision 3).
- Results: with a type: cilium 5,198, kubernetes 9,148, pandas 4,789, numpy 1,134; excluded CODEOWNERS requests at open / at ready: cilium 7,350 / 7,682, pandas 1,541 / 670 → results/data_truth.json @ fc9e735811cd0219e9f3a42982c3518bfac8cbff (clean tree).
- Dataset hash: type mappings `sha256:1817bb63b28f83abc3025849351cfec81bcceb8def17036a1bda9c6f55a30309`, repos config `sha256:70921de97acbe1c207b65d150326788daeb63846fa532618e0ecf0d1815932a1`
- Notes / decisions / problems:
  - First run counted cilium code-owner requests made when a draft is marked ready for review as human requests (11,795 `human_request`); with the ready-for-review rule 4,812 remain. Requests after later pushes cannot be told apart (no push times); `reviewer_evidence` keeps `reviewed` separate.
  - Other fixes before the record: pending (unsubmitted) reviews no longer count; the issue-mention regex no longer matches hex colours (`#12ab34`).
  - Types are imbalanced (kubernetes mostly refactor, pandas mostly docs, `dependency` nearly empty because dependency bots label their own PRs): Phase 2 reports macro-F1 per class and per repo.
- Next: 1.5

### 2026-11-02 — Task 1.5 — Dataset, splits, spot check, test freeze
- Status: done
- What changed: `prpilot_data/dataset.py` (stage E: one row per PR, open-time inputs, targets incl. reverted from the Phase 0 revert scan, `flagged` / `diff_inflated`, time splits, cross-split duplicate removal, leakage check, Parquet + hashes), `spot_check.py`, `data/config/frozen_test.toml` (the build refuses to change the test split), data card `data/README.md`; pyarrow 25.0.1 in the `data` group.
- Results: train 15,810 / val 3,355 / test 9,573 PRs; flagged 21.8% / 8.3% / 8.3%; reverted 120 / 18 / 40; 27 cross-split duplicate groups (train 63 and val 44 rows dropped) → results/data_dataset.json @ 6f74e6f5c0d184eb424babef912766f704b54593 (clean tree).
- Dataset hash: **test (frozen) rows_hash `sha256:e5f4ddc9205b06e90dcacf53ff53d8bd1383e562717eca3e8168fa4fe42abdd8`**, file `sha256:5787eaedc5f9633b80fc4ffa0cc84bff4a5d848bee9197598a7d36bc324012e7`; train rows_hash `sha256:3ef2e8adc06eb747feb2a515a9b74dab0a572f8e4cd454650f41a411c5273986`; val rows_hash `sha256:65a2b30e066c1beb3bea9319e182f297f1b17cad4cbfdb25b50ede1a4a01b769`
- Notes / decisions / problems:
  - Spot check (§4.6): owner reviewed 40 random train/val PRs (cilium 14, kubernetes 13, pandas 13, seed 0) on GitHub on 2026-11-02; labels and actors, reviewers and issues all correct. Test split not sampled.
  - Duplicates are recurring cilium PRs (release README updates, renovate updates); only older copies are dropped, so the test split is never reduced.
  - `author_association` is GitHub's current value (no open-time source); documented in the data card.
  - Test split frozen 2026-11-02, before any training or prompt tuning (hard rule 4).
- Next: PR to `main`, then the Phase 1 review stop before Phase 2

## Phase 2 — Baselines and eval harness

### 2026-11-02 — Task 2.1 — Issues, labels and open-time candidate lists
- Status: done
- What changed: `prpilot_data/issues.py` (every issue per repo with created/closed times, labels with creation time; query `issues-v1`), `prpilot_data/candidates.py` (labels created before open and applied by people >=10 times on PRs before 2025; top-20 reviewers from path history of PRs merged before open, never the author or bots; top-20 open issues by BM25, issues named in the PR first).
- Results: issues cilium 12,448 / kubernetes 49,951 / numpy 14,159 / pandas 28,667, all closing issues found → results/data_issues.json @ 3fe6ae2 (clean). Val ceilings: a true reviewer in the top 20 for 91.6% (top 3: 57.9%, n=3,113); a linked issue in the top 20 for 89.8% (top 5: 88.9%, n=679); 95.2% of target labels in the list → results/data_candidates.json @ 0ee5e07 (clean). Test ceilings not computed (hard rule 4).
- Dataset hash: candidates val rows_hash `sha256:199e2c3e22517221e86f14b7cfb7591dd5a47f96468b5e8b448176f280dc7162`
- Notes / decisions / problems: IDF uses issues created before 2025 only, so later text never shapes earlier rankings.
- Next: prompt packs

### 2026-11-02 — Task 2.2 — Prompt packs, targets, eval harness, rules baseline
- Status: done
- What changed: prompt packs (`prpilot_data/prompts.py`, `prompts-v1`): every PR's exact messages rendered once and hashed, so every model sees byte-identical input; PR body capped at 6,000 chars and the file list at 100 (the uncapped max was 101k tokens). Targets files (`targets.py`). `eval/`: its own uv project and lock, no app imports, JSON Schema exported from `TriageOutput`, the metrics of roadmap §5.3 with bootstrap CIs (1,000 resamples, seed 0) and exact McNemar. Rules baseline (`rules_baseline.py`).
- Results: val pack 3,355 PRs, prompt tokens median 2,913 / p95 8,346 / max 11,205, file `sha256:2306c061ee4ad3a4887c47e13d02135fb7710fd97ca7426e8af63bbbecc992fe` → results/data_prompts.json @ b72c196 (clean); targets → results/data_targets.json @ 043f3a3 (clean). Rules on val: maintainer match 48.0% (95% CI 46.1–49.9, n=2,435) → results/baseline_rules_val.json @ b48307e (clean).
- Dataset hash: val targets `sha256:24409ffbf6e0d6403cf8f49b17a32212dd4492331b177fb25fc8bc006dc67dc5`
- Notes / decisions / problems: harness and app validators agree on 16 hand cases (tests/test_eval_agreement.py) and on all 150 smoke replies. Phase 3 needs a max sequence length of ~12k.
- Next: model baselines on Explorer

### 2026-11-13 — Task 2.3 — Untuned Qwen3-1.7B and teacher baselines (Explorer)
- Status: done
- What changed: `training/baselines/vllm_replies.py` + `training/slurm/baseline_replies.sbatch`: one H200, greedy, no constrained decoding, no retries; replies + meta with the pack hash and code commit.
- Results (val, n=2,435 typed PRs): Qwen3-1.7B thinking off: maintainer match 34.7% (CI 32.8–36.6), valid JSON 89.6% (job 11852470, 0.053 GPU-s/PR) → results/baseline_qwen3-1.7b_val.json @ bd0cef2 (clean). Teacher thinking on: 62.8% (CI 60.8–64.6), type macro-F1 0.52, valid JSON 98.8% (job 11852471, 1.09 GPU-s/PR) → results/baseline_teacher_val.json @ 1b732c8 (clean). Teacher vs rules: 538 vs 178 discordant, p<1e-6.
- Dataset hash: val pack as in 2.2
- Notes / decisions / problems: the vLLM env on scratch lost files to the purge (`packaging`, `libbz2`); rebuilt from the lock in ~/prpilot-keep (job 11852395). Teacher labels for all of train would take ~5 h on one H200.
- Next: system pass, frontier baseline

### 2026-11-14 — Task 2.4 — System pass: Qwen3-1.7B through the agent with retries
- Status: done
- What changed: `training/baselines/agent_replies.py` + `training/slurm/agent_replies.sbatch`: the real Pydantic AI triage agent (2 retries, both validation layers) against `vllm serve` on one H200; agent env from `uv.lock` (`setup_agent_env.sh`).
- Results (all val, job 11874334): valid after retries 70.7% (2,373/3,355); retry rate 58.9%; rescue rate 50.3% (993/1,975); maintainer match 36.3% (CI 34.3–38.3) → results/baseline_qwen3-1.7b-system_val.json @ c3ecb66 (clean).
- Dataset hash: val pack as in 2.2
- Notes / decisions / problems: most failures are labels outside the repo list (the model writes the type, e.g. `docs`, as a label). First submit failed because Explorer sets `http_proxy` with no `no_proxy` (job 11874104); fixed in the sbatch.
- Next: Gemini

### 2026-11-14 — Task 2.5 — Frontier baseline: Gemini 3.8 Flash on Vertex AI
- Status: done
- What changed: `training/baselines/vertex_replies.py` (Vertex only, refuses AI Studio keys; free `countTokens` estimate first; hard cap reserving the worst case per request) and `vertex_agent_replies.py` (same agent and retries on Vertex). Seeded 500-PR val sample (`prompt-sample`, seed 0) and `prpilot_eval score --only` so every baseline is scored on the same PRs.
- Results (500-PR sample, n=363 typed): Gemini raw maintainer match 53.4% (CI 48.3–58.9), valid JSON 88.8%, $1.97 total → results/baseline_gemini-3.8-flash_val_s500.json @ 6f1f595 (clean). Gemini with retries: 59.2% (CI 54.2–64.5), valid after retries 100%, rescue 68/68, $2.36 total → results/baseline_gemini-3.8-flash-system_val_s500.json @ 1b732c8 (clean). Same sample: teacher 60.1%, rules 48.5%. Teacher vs Gemini raw: 34 vs 10, p=0.0004; teacher vs Gemini with retries: 18 vs 15, p=0.73.
- Dataset hash: sample `sha256:6e70fa0e9aa750671d1d8e451ccd7a8e539beede181bf114c2a7b9c40f3ff634` → results/data_prompts_val_s500.json @ e573529 (clean)
- Notes / decisions / problems: online requests instead of batch (batch needs a GCS bucket and up to 24 h for ~$2 saving on 500 PRs). Prices from the Vertex AI pricing page (global, introductory through 2026-12-31): $0.75 input / $3.75 output per 1M, thinking billed as output; thinking level LOW (lowest allowed), temperature at the model default. Gemini's invalid replies: issue numbers written as `#33550` in the JSON (26), code fences (14), invented types (16). Costs come from usage metadata; the bill is the truth.
- Next: summary, smoke suite

### 2026-11-14 — Task 2.6 — Baseline summary and CI smoke suite
- Status: done
- What changed: `prpilot_eval summary` → results/baselines_val.json (headline metrics, system retry and rescue rates, cost per PR, every McNemar pair within a PR set). Smoke suite: 50 val PRs with recorded replies from Qwen3-1.7B, the teacher and Gemini (`scripts/make_smoke_suite.py`, no PR text committed); CI re-scores them and re-runs the app validator (~1 s). The harness now lists `dirty_paths` and ignores files under `results/` when judging a clean tree; reports scored after another report was written were re-scored.
- Results: results/baselines_val.json @ 1b732c8 (clean).
- Dataset hash: n/a
- Notes / decisions / problems: no test-split numbers in Phase 2 (hard rule 4); the test split is scored once, at the end.
- Next: PR to `main`, then the Phase 2 review stop before Phase 3

## Phase 3 — Fine-tuning on Explorer

### 2026-11-15 — Task 3.1 — Teacher labels on the train split
- Status: done
- What changed: `vllm_replies.py --shard I/N`; the teacher (thinking on, 8,192 max tokens, text-only) over the train pack in 4 parallel H200 jobs (11878148–11878151), code 7bca2d3.
- Results: 15,810/15,810 train PRs answered (3,953 / 3,953 / 3,952 / 3,952), 1.02–1.09 GPU-s per PR, ~1 h 15 min per shard. Replies in data/processed/replies/teacher_train_s*of4_* and ~/prpilot-keep.
- Dataset hash: train pack `sha256:c56311b9a3311e238b6cd2b1d7ba7d4e97b83dc8beb187ccc553794b640cd387`
- Notes / decisions / problems: none.
- Next: 3.2

### 2026-11-15 — Task 3.2 — Fine-tuning data (sft-v1)
- Status: done
- What changed: `prpilot_data/sft.py` + `sft-data`: keep a PR when the teacher's reply is valid and its type equals the maintainer type; keep the teacher's summary, risk and rationale; overwrite labels, reviewers (truth ∩ candidates, candidate order, max 3) and related_issues (truth ∩ open issues) with the maintainer truth; every example must pass both validation layers. `training/sft/format.py`: the training prompt is the chat-template call vLLM makes (thinking off), loss on the answer only; `--check` compares each example with the template's rendering of the whole conversation; `tests/test_sft_format.py` is the format-match test (roadmap §6.1).
- Results: train kept 8,115 of 12,246 typed PRs (66.3%); agreement on valid typed replies 67.1% (8,115/12,096); dropped: 3,981 disagree, 150 invalid, 3,564 no maintainer type → results/data_sft_train.json @ 7bca2d3 (clean). Val kept 1,529 of 2,435 (62.8%) → results/data_sft_val.json @ d9cc417 (clean). Format check: 8,115/8,115 match the chat template; no example over 12,288 tokens.
- Dataset hash: sft_train.jsonl `sha256:0d568886665a…`, sft_val.jsonl `sha256:3cad66cfb4eb…` (full hashes in the results files)
- Notes / decisions / problems: 695 of 8,115 rationales (8.6%) name a label or reviewer the truth replaced. Thin types in train: dependency 34, other 64.
- Next: 3.3

### 2026-11-15 — Task 3.3 — LoRA training, base run
- Status: done
- What changed: `training/sft/train.py` (Hugging Face Trainer + PEFT, not TRL SFTTrainer; same answer-only loss), `configs/base.toml`, `train_lora.sbatch`, training env (torch 2.13.0+cu126, transformers 5.22.1, peft 0.21.2, accelerate 1.15.0, mlflow-skinny 3.17.2; lock in ~/prpilot-keep). bf16 LoRA r16 / α32 / dropout 0.05 on all attention and MLP projections, lr 2e-4 cosine, 3% warmup, 2 epochs, effective batch 16, max length 12,288. The MLflow run records data and config hashes, base revision, code commit and packages. `vllm_replies.py --lora-dir` scores every checkpoint of a run with the base loaded once.
- Results: job 11884071, 1 h 31 min on one H200, 1,016 steps at ~5.2 s/step; eval loss (300 val examples) 0.738 (step 200) → 0.653 (step 1,016). Val, n=2,435 typed: checkpoint 1016 maintainer match 69.2% (CI 67.3–70.9), valid JSON 99.6%, label F1 0.492, reviewer@3 0.432, issue@5 0.828 → results/student-base_val_checkpoint-1016.json @ 73e2252 (clean); checkpoints 200–1016 in results/student-base_val_checkpoint-*.json.
- Dataset hash: as in 3.2
- Notes / decisions / problems: deviations approved before coding: bf16 LoRA instead of 4-bit QLoRA (1.7B fits easily on an H200) and 12,288 max length instead of 4,096 (prompts reach ~11.8k tokens). First trial failed: MLflow 3.17 refuses the file store without `MLFLOW_ALLOW_FILE_STORE=true`, and transformers 5.22 dropped `warmup_ratio` (a fraction is now passed as `warmup_steps`); fixed in 3bb0182. Run-to-run noise: `final` and checkpoint 1016 have the same weights but score 69.0% vs 69.2% (vLLM batching), so differences of ~0.2 points are noise. Reviewer@3 fell below the teacher (0.545) and rules (0.579): the overwrite left 16.8% of training answers with no reviewers (mean 1.26 of 3).
- Next: 3.4

### 2026-11-16 — Task 3.4 — Fill variant and checkpoint choice
- Status: done
- What changed: `sft-data --fill` (sft-v2-fill): truth first, then the teacher's own valid picks (reviewers up to 3, issues); same 8,115 / 1,529 examples, same types and labels. `train_lora.sbatch` takes `SFT_TAG=_fill`. Same config as 3.3.
- Results: empty reviewer lists in training answers 16.8% → 0.9%, empty issue lists 79.0% → 49.7% → results/data_sft_train_fill.json @ 497e770 (clean), results/data_sft_val_fill.json @ 165582b (clean). Training job 11888117 (1 h 27 min), eval loss 0.705 → 0.624. Val fill checkpoint 1016: maintainer match 69.0% (CI 67.1–70.7), reviewer@3 0.549 (CI 0.532–0.568), issue@5 0.890, label F1 0.491, valid JSON 99.5% → results/student-fill_val_checkpoint-1016.json @ 4dbbc7e (clean). Base 1016 vs fill 1016 on maintainer match: 34 vs 29 discordant, p=0.61 → results/student_val.json @ 19b7fec (clean).
- Dataset hash: sft_train_fill.jsonl `sha256:1457dcac75d5…`, sft_val_fill.jsonl `sha256:af78dea9c2ad…`
- Notes / decisions / problems: **Chosen checkpoint: fill 1016 (owner decision, 2026-11-16).** By the strict rule (maintainer match first) base 1016 leads by 0.2 points, which is within run-to-run noise (p=0.61); the tie was broken on reviewer@3 (+0.117) and issue@5 (+0.062). Adapter `sha256:b127de9b…7f525a83`. Val metrics and `selected_checkpoint` logged to the fill MLflow run (job 11910718).
- Next: 3.5

### 2026-11-16 — Task 3.5 — Student system pass on validation
- Status: done
- What changed: `agent_replies.sbatch` with `LORA=<checkpoint>` serves the base + adapter under the name the agent calls; `--adapter` records the adapter hash.
- Results (all val, job 11898279): valid after retries 97.2% (3,260/3,355), valid on the first request (both layers) 96.6%, retry rate 3.4%, rescue rate 16.7% (19/114), maintainer match 69.0%, 204 s for 3,355 PRs → results/student-fill-system_val.json @ 19b7fec (clean). Untuned Qwen3-1.7B (2.4): 70.7% valid after retries, 58.9% retry rate.
- Dataset hash: val pack as in 2.2
- Notes / decisions / problems: the 95 failures are almost all cilium labels outside that PR's label list (e.g. `area/hubble`); in 68 of them the model repeats its answer after the error message (it was never trained on a retry turn), hence the low rescue rate.
- Next: 3.6

### 2026-11-16 — Task 3.6 — Test split, run once for every system
- Status: done
- What changed: test pack copied once (hash checked); rules, untuned Qwen3-1.7B (raw + system), teacher (4 shards, joined with `scripts/join_shards.py`) and student fill 1016 (raw + system) on all 9,573 test PRs; Gemini 3.8 Flash (raw + system) on a seeded 500-PR test sample (`prompt-sample --split test`, drawn from the pack alone). Scored once, together.
- Results (all test, n=5,562 typed PRs) → results/student_test.json @ 0cf4899 (clean):
  - Student: maintainer match 70.3% (CI 69.1–71.5), type macro-F1 0.640, label F1 0.376, reviewer@3 0.572, issue@5 0.883, valid JSON 98.7% (unconstrained, first attempt), 0.048 GPU-s per PR.
  - Student with retries: 70.4% (CI 69.3–71.7), valid after retries 87.2%, retry rate 14.2%.
  - Teacher 65.0% (CI 63.8–66.3), 0.951 GPU-s per PR; untuned Qwen3-1.7B 36.1% (with retries 38.1%, valid after retries 60.4%); rules 53.0%.
  - McNemar on maintainer match: student vs teacher 481 vs 191 (p<1e-6); vs untuned base 2,090 vs 191 (p<1e-6); vs rules 1,290 vs 329 (p<1e-6).
  - 500-PR sample (n=279 typed): student with retries 68.1%, Gemini with retries 68.1% (19 vs 19, p=1.0), Gemini raw 63.4% (student with retries vs Gemini raw: 24 vs 11, p=0.04), teacher 65.6%; Gemini reviewer@3 0.660 vs student 0.534. Gemini cost $1.79 raw + $1.93 with retries ($0.0036 / $0.0039 per PR).
- Dataset hash: test pack `sha256:f1caef7782bd…`, sample `sha256:dff996a1b1e1…` → results/data_prompts_test_s500.json @ 28f7dbb (clean)
- Notes / decisions / problems: the student beats the teacher on type at ~1/20 of its GPU time, but stays below the rules baseline on reviewer@3 (0.617) and issue@5 (0.902). Held-out repo numpy (never in training): maintainer match 55.0% (teacher 53.1%, rules 53.5%), but label F1 0.112 (teacher 0.25): the student writes labels in other repos' styles (`Testing`, `component: Build`) instead of numpy's own (`05 - Testing`), so 1,089 of 3,603 numpy PRs fail the system pass; this is why valid after retries is 87.2% on test vs 97.2% on val. A deterministic label filter in the Phase 5 executor would turn these into "no label suggested"; these numbers stay as measured.
- Next: 3.7

### 2026-11-16 — Task 3.7 — GGUF export (Q4_K_M) and re-test on the test split
- Status: done
- What changed: llama.cpp v0.8.0 (commit 8642541b, tarball `sha256:51b1e025…`) built on scratch: portable CUDA `llama-server` for the H200s and a CPU-only `llama-quantize`, both without OpenSSL (`setup_llamacpp.sh`). `export_gguf.sbatch`: merge fill 1016 into the base (bf16, `merge_lora.py`), convert with llama.cpp's converter, quantize to Q4_K_M; `export_meta.json` in ~/prpilot-keep. `gguf_replies.sbatch` serves the file with `llama-server` (16 slots) and runs the raw runner (`openai_replies.py`) or the unchanged agent.
- Results: Q4_K_M file 1,107,408,736 bytes (1.05 GiB, 5.12 bits per weight) `sha256:b6e18672b4043587baa67c907d120c7ab02f719ec20c99b50e48ce7fa8ff53bb` (bf16 GGUF 3.4 GB, `sha256:bc222596…`). Prompt rendering: prompt token counts equal vLLM's on 9,572 of 9,573 test PRs (numpy #23644: 3,809 vs 3,807). All test, n=5,562 typed → results/student_gguf_test.json @ 3baac29 (clean):
  - GGUF raw: maintainer match 67.7% (CI 66.5–68.9), type macro-F1 0.631, label F1 0.389, reviewer@3 0.547, issue@5 0.762, valid JSON 90.4% (jobs 11914957, 3,349 s on one H200). vs the bf16 student: 158 vs 303 discordant, p<1e-6 (−2.6 points); vs the teacher: 504 vs 359, p<1e-6.
  - GGUF with retries: maintainer match 69.7% (CI 68.5–70.9), valid after retries 75.9%, first attempt 75.6%, retry rate 24.4% (job 11914958). vs the bf16 student with retries: 174 vs 218, p=0.03 (−0.7 points).
- Dataset hash: test pack as in 3.6
- Notes / decisions / problems: the loss is almost all numpy (held out): maintainer match 42.2% (bf16 55.0%), valid JSON 75.0% (bf16 97.2%). Quantized, the model often writes numpy's numbered labels as bare numbers (`"labels": [04, 56]`), which is not valid JSON. On the three training repos it is close to bf16 (cilium 73.3% vs 73.7%, kubernetes 69.4% vs 68.3%, pandas 84.5% vs 86.2%). Not tried: Q8_0 (about 1.8 GB, slower on CPU), constrained decoding in production (must be reported separately from the model's validity). The GGUF numbers were measured on an H200 with llama.cpp's CUDA backend; the CPU backend used in deployment is checked in the Phase 6 shadow replay. Export failures on the way (missing `sentencepiece`, CUDA build of `llama-quantize` on a CPU node, OpenSSL version differing between nodes) fixed in 125c1d9 and a80e3b2.
- Next: Resume Numbers (owner approval), PR to `main`

## Phase 4 — Untrusted input: injection, PII, secrets

### 2026-11-16 — Task 4.1 — Screening step
- Status: done
- What changed: `prpilot_core/screening/screen.py`: `screen(pr, guard, pii, scan_secrets, threshold)` runs before any model call. PromptGuard 2 86M scores each model-visible field (title, body and file list capped as the triage prompt caps them, diff; max over overlapping 512-token windows). A PR is quarantined (no model call, no proposals) when the body, file list or diff reaches 0.9; otherwise Presidio redacts PII in the title and body (`pii.py`) and detect-secrets 1.5.0 redacts secrets in the diff (`secrets.py`, each file scanned under its own name so detect-secrets' file filters apply). PromptGuard can load from a local copy of the pinned snapshot and run on a GPU.
- Results: n/a (measured in 4.2–4.5)
- Dataset hash: n/a
- Notes / decisions / problems: **Owner decisions (2026-11-16):** the title is scored and recorded but does not quarantine on its own, threshold 0.9, both chosen on validation only (4.2). Presidio runs on prose only (task 0.7) with entities and threshold 0.5 fixed in code before evaluation; URLs and GitHub @handles are kept (public, and reviewer and issue signals). detect-secrets' ad hoc line scan reports every word as "high entropy", so the normal file scan is used. A first version scored the full changed-file list; cilium vendoring PRs (95k–157k characters of file names) then overflowed the teacher classifier's 32,768 tokens, and the screened text no longer matched the prompt; fixed in 9e9e88d by capping it like the prompt.
- Next: 4.2

### 2026-11-16 — Task 4.2 — Red-team set and injection results
- Status: done
- What changed: `prpilot_data/redteam.py` + `redteam --split val|test`: poisoned copies of seeded PRs, each payload asking for a detectable canary (`canary-<id>` label or reviewer, or `CANARY-<id>` in the summary or rationale). Locations: title, body, hidden HTML comment, code comment in the diff. Commit messages are not part of the model input or the dataset, so the title replaces them (owner, 2026-11-16). 24 templates in two disjoint sets: dev (val PRs, for choosing the threshold) and test (test PRs, reported once). `training/screening/guard_scores.py` scores fields on an H200; `prpilot_core/screening/classifier.py` is the LLM-classifier prompt (one word, INJECTION or SAFE); `prpilot_data/redteam_report.py` + `redteam-report` compute every metric after checking each Explorer output against the hash of the pack it ran on. Attack success is counted two ways: the canary anywhere in the agent's output, and the canary reaching an action (a reply that passed both validation layers, with the canary in the field it targets).
- Results (test: 2,000 poisoned from 500 test PRs, 9,573 clean test PRs) → results/redteam_test.json @ 6f7ab0d (clean):
  - PromptGuard 2 (body, file list, diff; 0.9): blocked 11.1% (CI 9.8–12.6; 222/2,000), false positives 0.25% (24/9,573). Direct payloads 22.0%, task-specific 0.2%; by location: diff comment 17.4%, hidden comment 15.6%, body 11.2%.
  - Teacher as classifier (Qwen3.5-122B, thinking off): blocked 54.0% (CI 51.8–56.2), false positives 0.0% (0/9,573); title payloads 21.8%.
  - End to end (student fill 1016 through the agent): canary reached an action in 0.40% (8/2,000) with no screening and 0.40% (8) with PromptGuard; 0.25% (5) with the teacher classifier. Canary anywhere in the output: 0.90% (18) without screening, 0.90% with PromptGuard.
  - Validation (dev set, 1,200 poisoned, 3,355 clean) → results/redteam_val.json @ 44ecef9 (clean): PromptGuard at the chosen setting blocked 20.2%, false positives 0.12%; teacher classifier 61.1%, 0.0%; action success 0.33% without screening.
- Dataset hash: redteam_test.jsonl `sha256:37bb594dee35…`, prfields_test.jsonl `sha256:1588e4e0fc0c…` → results/data_redteam_test.json @ 7862857 (clean); dev set → results/data_redteam_val.json @ 9e9e88d (clean)
- Notes / decisions / problems: threshold choice on validation (owner, 2026-11-16): with all fields at 0.9, PromptGuard flagged 2.3% of clean val PRs, 74 of 77 on the title alone ("Remove unused code", "Forbid memory limit decrease" score ~0.999); without the title, 0.12%. PromptGuard's block rate halved from val to test, the effect of the held-out templates. All 8 test attacks that reached an action were text canaries echoed into the summary (7 from the title); no label or reviewer canary reached an action, on val or test. The model and the label/reviewer validator do most of the work; PromptGuard adds little on this set. PromptGuard on Explorer loads a copy of the pinned snapshot from scratch (the gated model has no token there).
- Next: 4.3

### 2026-11-17 — Task 4.3 — Secrets on synthetic diffs
- Status: done
- What changed: `prpilot_data/secrets_eval.py` + `secrets-eval`: 600 fake secrets in 12 formats (50 each, generated at run time, nothing secret-shaped in the source) inserted as added lines into seeded test diffs; detect-secrets scored on the inserted lines; flag rate on every unmodified test diff.
- Results → results/secrets_synthetic.json @ 7d1f18f (clean): recall 93.5% (CI 91.2–95.2; 561/600); precision in the seeded diffs 95.9% (891 findings on inserted lines, 38 elsewhere); unmodified test diffs flagged 1.4% (133/9,573: secret keyword 62, hex high entropy 61). Recall 100% for AWS key IDs, classic GitHub tokens, Slack, Stripe, OpenAI, JWT and private keys; lowest for password assignments 78%, Google API keys 84%, fine-grained GitHub tokens and plain hex secrets 86%, AWS secret keys 88%. ~0.09 s per diff.
- Dataset hash: test diffs as in 3.6, seed 0
- Notes / decisions / problems: the unmodified diffs are unlabelled, so a flagged real diff may hold a real (often test-fixture) secret. On Windows, detect-secrets opens files in the system code page; the run used `PYTHONUTF8=1`.
- Next: 4.4

### 2026-11-17 — Task 4.4 — Screening latency on CPU
- Status: done
- What changed: `screening-latency`: the full `screen()` step on CPU on 100 seeded clean test PRs, every PR fully screened.
- Results (laptop, 14-core Intel, Windows 11) → results/screening_latency.json @ 044a81c (clean): per PR p50 5.6 s, p95 30.0 s, mean 9.8 s; PromptGuard p50 5.4 s, p95 29.6 s (~97% of the time); Presidio p50 0.06 s; detect-secrets 0.06 s.
- Dataset hash: test split as in 3.6
- Notes / decisions / problems: PromptGuard time grows with diff length (one 86M forward pass per 512-token window). With an 11.1% block rate on test, this is a cost to weigh when sizing the Phase 6 worker (options: fewer fields, the 22M model, or a GPU).
- Next: 4.5

### 2026-11-17 — Task 4.5 — Promptfoo red-team suite in CI
- Status: done
- What changed: `redteam/promptfoo/` (Promptfoo 0.125.1, Node 22): 30 synthetic injected PRs (no real PR text committed; dev templates only; built by `scripts/make_promptfoo_suite.py`) run through the real `screen()`; a case fails if its canary reaches the text the model would see. New CI job `redteam-screening` (CPU torch on Linux via a pytorch-cpu index in `pyproject.toml`; PromptGuard downloaded with the `HF_TOKEN` repository secret); `workflow_dispatch` input `screening: off` for the one-time check.
- Results: green on the first run on the draft PR (a2ad69d). Local check of the provider: screening on, 0/30 canaries reach the prompt; off, 30/30. One-time check (2026-11-17, CI #16, manual run on `main` @ 971b404 with `screening: off`): `redteam-screening` failed as required; Promptfoo evaluated 30/30 cases and every case shown in the log failed with its canary in the prompt (the final summary lines were not captured).
- Dataset hash: n/a
- Notes / decisions / problems: only cases PromptGuard quarantines are kept, so the suite is a regression test of the screen, not a measure of it (that is 4.2). Of 216 synthetic candidates, 67 were quarantined, all direct payloads: no task-specific payload is in the suite because PromptGuard blocks none. The approved local screening-off check with a GGUF model was replaced by the CI dispatch run, since Node is not installed on the laptop.
- Next: 4.6

### 2026-11-17 — Task 4.6 — PII: Presidio on the Kaggle essays (F5)
- Status: done
- What changed: `prpilot_data/pii_eval.py` + `pii-eval`: Presidio with the app's fixed settings (entities PERSON, EMAIL_ADDRESS, PHONE_NUMBER, IP_ADDRESS, CREDIT_CARD, IBAN_CODE, US_SSN; threshold 0.5; en_core_web_lg 3.8.0; @handles kept) on the Kaggle "PII Data Detection" train essays; character spans mapped to BIO token labels and Presidio types to Kaggle types; scored with the competition's micro F5 on (document, token, label). A reference configuration adds URL and LOCATION.
- Results (6,807 essays, 2,739 labelled tokens) → results/pii_kaggle.json @ 110ec4e (clean): **app configuration F5 0.685** (precision 0.232, recall 0.743; entity-type F5 0.691). Recall by type: EMAIL 1.00 (39), NAME_STUDENT 0.81 (2,461), PHONE_NUM 0.57 (21), ID_NUM 0.00 (79), URL_PERSONAL 0.00 (111, URLs not redacted by design), STREET_ADDRESS 0.00 (22), USERNAME 0.00 (6). Reference with URL and LOCATION: F5 0.656 (precision 0.130, recall 0.783; URL_PERSONAL recall 0.99). 0.10 s per essay on the laptop.
- Dataset hash: train.json `sha256:8276cd44f3b2e…` (full hash in the results file; data in data/raw/kaggle_pii/, gitignored, owner-downloaded)
- Notes / decisions / problems: the data is student essays, not code, so this measures the redactor on prose, which is where the app uses it. Precision is low because Presidio's PERSON finds every name (cited authors, public figures) while the dataset labels only students; F5 weights recall 25x, so this costs little. Turning on URL and LOCATION lowers F5: essays contain many non-personal URLs, and LOCATION does not match the street-address labels. ID numbers in these essays (student IDs) match none of Presidio's ID recognisers. Entities and threshold were fixed before this run and not tuned on it.
- Next: Resume Numbers (owner approval), PR to `main`; after the merge, the one-time screening-off CI run (4.5)

## Phase 5 — Approval-gated writes and memory

### 2026-11-17 — Task 5.1 — Postgres state and the approval lock
- Status: done
- What changed (cc30cbe): Postgres 17.10 (pinned by digest; docker compose locally, a service container in CI), SQLAlchemy 2.1.6 async with asyncpg 0.32.0, Alembic 1.20.1 (`migrations/`, initial schema; CI runs `alembic upgrade head`, `alembic check` and `downgrade base`). Tables: deliveries, runs (one per repo, PR and head SHA), proposals (content and the SHA-256 of its canonical JSON), approvals (one per action, bound to the hash), executions (one per approval, claimed before the write), refusals, reviewer history and label stats. `prpilot_core/executor.py`: `approve()` and `execute()`. Refusals are logged as `unknown_proposal`, `tampered`, `unknown_action`, `unapproved`, `rejected`, `hash_mismatch`, `not_allowlisted`, `replayed` or `error`. `EXECUTOR_DRY_RUN` defaults to true.
- Results: `tests/test_lock.py`, 11 tests. They cover every refusal in roadmap §8.3 (unapproved, tampered, non-allowlisted, replayed), plus a rejected action, a tampered proposal with a recomputed hash, partial approval, a failed write that is not retried, and a dry run.
- Dataset hash: n/a
- Notes / decisions / problems: owner decisions (2026-11-17): SQLAlchemy, asyncpg and Alembic; tests on a real Postgres; a static admin token for approvals for now (ADR 0005). The unique execution row stops two simultaneous executes, but no test runs them at the same time.
- Next: 5.2

### 2026-11-17 — Task 5.2 — GitHub read interface: live and open-time replay clients
- Status: done
- What changed (584eb5c): `GitHubClient` protocol (`get_pr`, `get_diff`, `list_changed_files`, `get_repo_labels`, `search_issues`). `LiveGitHubClient`: GitHub App installation tokens (signed JWT, token cached), ETag caching, rate-limit waits (up to 120 s), retries on 5xx (3) and paging. `AppWriter` holds the write methods, for the executor only. `ReplayGitHubClient`: serves only the dataset's open-time fields and the open-time candidate lists; anything else raises `NotInReplay`.
- Results: `tests/test_github_clients.py`, 8 tests (replay returns only open-time facts; live paging, ETag 304s, rate-limit waits and retries, giving up, write payloads, App auth).
- Dataset hash: n/a
- Notes / decisions / problems: the replay client marks itself `snapshot`, so the pipeline uses its stored diff and candidate lists as they are, and a replayed prompt is the same as the evaluated one.
- Next: 5.3

### 2026-11-17 — Task 5.3 — Triage pipeline
- Status: done
- What changed (94cf664): `prpilot_core/pipeline.py` runs context → screen → [quarantined: stop] → triage (the student via Pydantic AI; 2 output retries, temperature 0, seed 0, thinking off) → validation fallback → reviewers from memory → comment → proposal. There is one run per head SHA, locked while it is processed (ADR 0004). `context.py` gathers everything the training prompts contained, with the same code (ADR 0006). `agents/fallback.py`: when the retries run out, it keeps the valid parts, drops labels and reviewers not in the repo's lists, and flags the proposal `partly_filtered`. `comment.py`: a deterministic triage comment. `memory.py`: reviewer history (the Phase 2 ranking formula) and label usage stats.
- Results: `tests/test_pipeline.py`, 9 tests (a hashed proposal; a quarantined PR never reaches the model; no second triage on redelivery; a new push is a new run; fallback; failed runs are retried; label stats; live reviewer ranking equals Phase 2's).
- Dataset hash: n/a
- Notes / decisions / problems: related issues stay deterministic. The optional LLM issue-search tool and its comparison were not built (owner, 2026-11-17; ADR 0006). The PR's first CI run found a bug: when a step raised, recording the failure crashed (it read the run's id after the rollback had expired it). In CI the secrets scan raised, because detect-secrets was not installed in the test job. Fixed with a test where a step raises; detect-secrets is now in the `dev` group.
- Next: 5.4

### 2026-11-17 — Task 5.4 — Webhook and approval API, queue and worker
- Status: done
- What changed (70164ec): `services/api/app.py` (FastAPI):
  - `POST /webhook`: checks the signature and dedups deliveries. Handles PR opened, reopened, synchronize and ready-for-review events; drafts are ignored.
  - `GET /runs`, `GET /proposals` and `GET /proposals/{id}`.
  - `POST …/actions/{action}/decision`: takes the content hash the approver saw.
  - `POST …/execute`: returns 409 on refusal; a real write updates memory.

  `prpilot_core/queue.py`: `EventQueue`, with an in-process implementation (Pub/Sub in Phase 6). `services/worker/handler.py`: triages sandbox repos only. `services/local.py`: everything in one process, with the student GGUF on llama.cpp.
- Results: `tests/test_api.py`, 8 tests (bad signatures rejected, one queue entry per delivery, other events and drafts ignored, token required, approve then execute writes once and updates memory, stale content refused, webhook to proposal through the queue, dry run by default).
- Dataset hash: n/a
- Notes / decisions / problems: none.
- Next: 5.5

### 2026-11-17 — Task 5.5 — Reviewer memory ablation
- Status: done
- What changed (6c88ef1): `prpilot_data/memory_ablation.py` + `memory-ablation`. It re-scores the student's existing system-pass replies (fill 1016), after the pipeline's fallback, under four reviewer rules: student only, student then memory, memory then student, memory only. Only `reviewers` changes. The rule is chosen on val; test is scored once.
- Results → results/memory_ablation.json @ 6c88ef1 (clean): reviewer@3 on val (n=3,113): student only 0.552 (CI 0.535–0.569), memory then student 0.577 (CI 0.560–0.595), chosen. Test, looked at once (n=7,250): student only 0.572 (CI 0.561–0.583), memory then student 0.612 (CI 0.601–0.622). Applied as the pipeline default in e18d423.
- Dataset hash: val replies `sha256:41c70e71…eaefa`, test replies `sha256:184456dd…2681f` (full hashes in the results file)
- Notes / decisions / problems: "memory then student" scores exactly the same as "memory only" on both splits, and "student then memory" the same as "student only". This holds by construction. The student may only pick from the eligible list, which is the memory ranking, so once the ranking comes first the student's picks never change the top 3. The chosen rule won the tie through the fixed rule order. In effect, the deployed reviewers are the memory ranking's top 3. For reference, the rules baseline scored 0.617 on test (3.6).
- Next: 5.6

### 2026-11-17 — Task 5.6 — Sandbox end-to-end run
- Status: done
- What changed: `docs/runbooks/sandbox-e2e.md` (b5bd9ba). The owner ran it on the laptop at b5bd9ba (clean tree), with:
  - llama.cpp b11960 (CPU) serving `student-fill-1016-Q4_K_M.gguf` (`sha256:b6e18672…53bb`);
  - Postgres in Docker;
  - the GitHub App `prpilot-dev-pratox1112` on `pratox1112/prpilot-sandbox`;
  - a Cloudflare quick tunnel.
- Results: sandbox PR #2 (a README change).
  - Dry run: a signed webhook created run 1 (head `da5dea9`). It was not quarantined (PromptGuard 0.001). The proposal had the label `documentation` and a comment, valid on the first attempt. The labels action was approved with its hash and executed with `dry_run: true`, and the PR did not change.
  - Real run (`EXECUTOR_DRY_RUN=false`): a second push created a new run and proposal. The labels and comment actions were approved and executed, and the label and comment appeared on the PR, posted by the App (issuecomment-6969023852). Executing the labels action again was refused with 409 `replayed`.
- Dataset hash: n/a
- Notes / decisions / problems: no reviewers were proposed, because the sandbox repo has no review history. Model latency was not measured in this run.
- Next: 5.7

### 2026-11-17 — Task 5.7 — Design records and tracing
- Status: done
- What changed: ADRs 0004 (async pipeline with its state in Postgres), 0005 (proposals and a separate executor) and 0006 (deterministic context, no LLM issue tool). `prpilot_core/tracing.py`: Pydantic AI's OpenTelemetry instrumentation, exported to Langfuse. It is off unless `TRACING=true`, and startup fails if it is on without the Langfuse keys. Spans only see screened text.
- Results: `tests/test_tracing.py`, 4 tests (off by default, with no spans; when on, agent and model spans are exported; the keys are required; endpoint and auth). Full suite with Postgres, installed as CI installs it: 332 passed, 1 skipped (transformers).
- Dataset hash: n/a
- Notes / decisions / problems: tracing was not turned on against Langfuse in the end-to-end run.
- Next: Resume Numbers (owner approval), PR to `main`; then the Phase 6 guidelines

## Phase 6 — GCP deployment and system design

### 2026-11-18 — Task 6.1 — Cloud Run services, images and the Pub/Sub queue
- Status: done
- What changed (87bd525, f6e1dec): the service is split into `api` (webhook and approval API), `worker` (Pub/Sub push endpoint, screening and triage) and `executor` (the only holder of the writing App's key). A `PubSubQueue` sits behind the `EventQueue` interface. The worker reads GitHub as a second, read-only App (`prpilot-reader-pratox1112`, sandbox only). There are images for app, worker (screening models baked in, loaded before the port opens) and model (llama.cpp CPU server with the Q4_K_M GGUF).
- Results: n/a
- Dataset hash: n/a
- Notes / decisions / problems: the dashboard service is deferred to Phase 7 (owner, 2026-11-18).
- Next: 6.2

### 2026-11-18 — Task 6.2 — Terraform and the CI plan
- Status: done
- What changed (cf4585d, 32de816, f9af348, 39b541b): `infra/terraform/`, one module per concern:
  - Artifact Registry, and the four Cloud Run services plus a migrations job;
  - Pub/Sub topic, push subscription, dead-letter topic and a 7-day hold subscription;
  - Cloud SQL Postgres 17 (db-f1-micro), Secret Manager, one service account per service;
  - a monitoring dashboard and an email alert on dead letters.

  State is in a GCS bucket. CI runs fmt, validate and plan through Workload Identity Federation, with a read-only identity. `docs/runbooks/gcp-deploy.md` covers deploy, pause and teardown.
- Results: n/a
- Dataset hash: n/a
- Notes / decisions / problems: Cloud Run's front end reserves `/healthz`, and a revision that failed for a missing image is not retried (the runbook says how to force one). ADRs 0007 and 0008.
- Next: 6.3

### 2026-11-18 — Task 6.3 — Tracing on the deployed path
- Status: done
- What changed (7218004): one `pr_run` trace per PR, with spans `context`, `screen`, `triage` (the model calls inside) and `proposal`. The Langfuse keys are Secret Manager secrets for the worker only. `-var tracing=true` turns tracing on.
- Results: the owner checked in Langfuse (2026-11-20) that the trace count matched the replayed runs.
- Dataset hash: n/a
- Notes / decisions / problems: ADR 0011.
- Next: 6.4

### 2026-11-19 — Task 6.4 — Replay tooling and the 50-PR pilot
- Status: done
- What changed (44441ab, 676f072, 3174024):
  - `replay/bundle.py`: open-time bundles (hard rule 11), mounted read-only into the worker from a GCS bucket.
  - `replay/send.py`: signed `pull_request.opened` webhooks, at most N PRs in flight.
  - `replay/measure.py`: outcomes, latency, and cost from Cloud Monitoring usage at pinned price-catalog SKUs (list prices, free tier not applied).
  - The worker triages a bundled PR from the bundle and refuses other repos.
- Results → results/pilot.json @ bbcd83a (the tree was dirty only with the report's own two output files). 50 val PRs (every 10th of the val sample), 3 in flight:
  - 50 proposed. 49 valid after retries, 1 rescued by the fallback, 1.04 model requests per PR.
  - Sender-clock latency: p50 132 s, p95 432 s. Throughput 1.0 PR/min.
  - Serving $1.39 ($0.0278 per PR); database $0.013.
  - Usage: model 6,847 instance-s at 4 vCPU and 8 GiB; worker 8,674 instance-s at 2 vCPU and 8 GiB.
- Dataset hash: n/a (bundle built from `prompts_val_s500.jsonl`)
- Notes / decisions / problems:
  - The cost was about 5 times the pre-pilot estimate: real diffs run up to about 6,000 tokens, and prefill on CPU dominates.
  - Server-clock latency is left out of the pilot. `pr_runs.updated_at` was stamped with `now()`, the start of the transaction that claimed the run, so it carried the claim time. Fixed with `clock_timestamp()` (3174024, with a test that fails without it).
  - Cloud Monitoring counted 46 push acks for 50 runs. Not explained (possibly metric delay).
- Next: 6.5

### 2026-11-20 — Task 6.5 — Burst test: 500 PRs at once, 10% redelivered
- Status: done (overloaded; not rerun, owner's decision)
- What changed (cc03f95, cd3ee08, 2b7ab61): `loadtest/burst.py` sends a whole bundle at once (20 parallel requests), then redelivers a seeded 10% with the same delivery ids. Database connections are bounded per service, and the database's `max_connections` raised to 50. Load runs raise capacity and attempts with `max_instances`, `model_min_instances` and `max_delivery_attempts`. The report reconciles unfinished PRs against the dead-letter subscription, read without consuming it.
- Results → results/burst.json @ 2b7ab61 (clean). 500 val PRs (231 cilium, 212 kubernetes, 57 pandas, none in the pilot) plus 50 redeliveries, against 10 workers and 10 models (8 GiB), with 20 delivery attempts:
  - Redeliveries: all 50 answered `duplicate`.
  - Duplicate proposals: 0 (168 PRs, 168 proposals).
  - Lost: 0. 168 PRs finished (167 proposed, 1 failed with its triage stored); all 332 unfinished are held in the dead-letter subscription. 23 PRs that finished are held there too.
  - Webhook to stored triage: p50 3,187 s, p95 7,261 s (the queue wait). Worker start to stored: p50 96 s, p95 330 s.
  - Throughput 1.26 finished PRs/min.
  - Pub/Sub push answers: 148 ack, 8,273 unreachable (429, no free instance), 95 server 5xx, 31 deadline exceeded.
  - Serving $4.79 ($0.0285 per finished PR); database $0.05.
- Dataset hash: n/a (bundle: 500 seeded from val, excluding the pilot's)
- Notes / decisions / problems:
  - The burst was larger than capacity times retry window. Every 429 counts as a delivery attempt, so waiting events used up 20 attempts in about 1 h 45 min, while the backlog needed about 5 hours.
  - The model ran out of memory at 8 GiB on long prompts (8.2–8.5 GiB used, 6 crashes between 23:43 and 00:15 UTC). It now has 12 GiB (af54c72, ac97661): 16 GiB fits only 7 instances in the region's 112 GiB per-service memory quota.
  - Cloud Monitoring counts 357 dead-lettered messages, and 355 were read from the hold subscription.
  - ADR 0007 covers the retry and ack-deadline behaviour.
- Next: 6.6

### 2026-11-20 — Task 6.6 — Shadow replay on the test sample (stopped at 103 PRs)
- Status: partial (stopped by the owner at 103 of 500 to save time)
- What changed (c004037, 23b8d5d):
  - `replay/shadow.py prompts`: rebuilds each bundled PR's prompt the way the worker does and checks it is byte-identical to the offline pack. 500/500 on the burst's val bundle. The owner ran it on the test bundle before the send; its output was not saved.
  - `replay/shadow.py replies`: writes the deployed runs as two reply files for the eval harness. "Model" is the agent's output; "proposal" is what a maintainer would see, with reviewers from the reviewer rule.
  - `/runs?output=true` returns the triage output, admin only.
- Results. The bundle is the 500-PR test sample in opening-time order; 9 workers and 9 models (12 GiB), 8 in flight. The 103 PRs sent are all numpy (held out of training).
  - → results/shadow.json @ bd3590a (clean):
    - 103 finished: 93 proposed, 10 failed with invalid output. 0 lost, 0 duplicates, nothing dead-lettered.
    - 44 valid after retries, 49 rescued by the fallback filter, 2.17 model requests per PR.
    - Screening redacted personal data in 28 prompts and secrets in 5.
    - Webhook to stored: p50 247 s, p95 953 s. Worker start to stored: p50 86 s, p95 335 s.
    - Push answers: 5 deadline exceeded, 17 unreachable.
    - Serving $2.54 ($0.0246 per PR); database $0.02.
  - → results/shadow_replay_model.json, shadow_replay_proposal.json and student-gguf-system_test_shadow103.json @ 23b8d5d (clean), scored by the eval harness on the same 103 PRs. Deployed model output, deployed proposal, then the offline GGUF on the H200:
    - Maintainer match (n=26): 57.7% (CI 34.8–77.8), 57.7%, 61.5% (CI 40.0–81.8).
    - Valid output: 90.3% in all three.
    - Reviewer@3 (n=53): 41.5%, 47.2%, 45.3%.
    - Issue@5 (n=22): 54.5%, 54.5%, 59.1%.
    - Label F1: 0.100, 0.100, 0.089.
    - Deployed model vs offline, exact McNemar: 1 vs 2 discordant, p = 1.0.
- Dataset hash: test pack `sha256:f1caef7782bd…`, sample `sha256:dff996a1b1e1…` (results/data_prompts_test_s500.json)
- Notes / decisions / problems:
  - The CPU backend matches the H200 GGUF measurement on these PRs, with no significant difference. The checked set is 103 numpy PRs, the repo the student is weakest on, so this is not an end-to-end quality figure for all repos. The offline test numbers (3.7) stay the model's quality figure.
  - The test split was looked at once, for these 103 PRs.
  - The result files are named `shadow*.json` and `burst.json`, not roadmap §9.3–9.4's `burst_test.json` and `shadow_replay.json`.
- Next: 6.7

### 2026-11-20 — Task 6.7 — Design records
- Status: done
- What changed: ADRs 0007 (Pub/Sub push, not synchronous), 0008 (Cloud Run, not GKE), 0009 (CPU GGUF, not a GPU endpoint), 0010 (Pydantic AI, not LangGraph) and 0011 (Langfuse Cloud and Cloud Monitoring). Roadmap §9.2's other decisions are ADRs 0003–0006.
- Results: full suite with Postgres: 356 passed (c004037).
- Dataset hash: n/a
- Notes / decisions / problems: the deployment is paused (`db_activation_policy=NEVER`); services scale to zero. Measured cost of this session's runs at list prices: burst $4.84, shadow replay $2.56.
- Next: Resume Numbers (owner approval), PR to `main`; then Phase 7 (owner's choice) or Phase 9
