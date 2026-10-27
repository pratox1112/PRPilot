# ADR 0003: Teacher model for distillation

- Status: accepted
- Date: 2026-10-27

## Context

The student (Qwen3-1.7B) is fine-tuned on full `TriageOutput` targets produced by a larger
teacher (roadmap §6.1). Requirements: a license that allows training on its outputs and
distributing the student; runs on what we have (Northeastern Explorer: one H200 141 GB per
job without a multigpu request, 8 h limit); agrees with maintainers on PR type often enough
to be worth distilling (task 0.6 go threshold: ≥ 60% on 200 train-window PRs).

Options considered (task 0.6, roadmap §3):
- **Gemini on Vertex AI** — strong, but Google's terms restrict using outputs to develop
  competing models (would need its own ADR), costs credits, and Gemini is also the frontier
  baseline, which would collapse "teacher-relative" and "frontier-relative" comparisons.
- **Llama 3.x 70B** — the license requires models trained on its outputs to carry "Llama" in
  their name.
- **Open-weights Apache-2.0 Qwen models** — no output-use restriction.

## Decision

Teacher: **`Qwen/Qwen3.5-122B-A10B-GPTQ-Int4` @ `30cd92cba9707a9aba09d1e490ed4b66b78e9606`**
(Apache-2.0, 122B total / ~10B active parameters, 4-bit GPTQ, 78.9 GB), served with
vLLM 0.31.0 on one H200, **thinking mode on**, temperature 0, prompt from
`prpilot_core.agents.triage_format` (identical to the student's).

Fallback (unused): `Qwen/Qwen3.8-27B-FP8` @ `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.

## Evidence

`results/feasibility_teacher_agreement.json` (code @ 5259fb3): type agreement with maintainers
on 200 random train-window PRs = **130/200 = 65.0%** (95% CI 58.2–71.3%) with thinking on,
62.5% with thinking off; first-attempt valid JSON 200/200 (thinking on). Most disagreement is
on the catch-all `other` type (2/31); without it agreement is 76%. 200 PRs take 146 s on one
H200 after a ~4 min load, so labelling the full train split fits one job.

## Consequences

- The teacher env must match Explorer's H200 driver (570, CUDA 12.8; no update planned):
  CUDA 12.9 builds of torch/vLLM plus NVIDIA forward compatibility (`cuda-compat` 13.0.2 /
  `cuda-compat-impl` 580.95.05). See `training/slurm/env.sh` and `setup_vllm_env.sh`.
- Gemini stays an independent frontier baseline (inference only, §5.2).
- The CI lower bound sits just under 60%; the agreement filter of §6.1 discards
  disagreements, so the `other` mapping (Phase 1) directly affects how much training data
  survives.
