# ADR 0009: Serve the student as a Q4_K_M GGUF on Cloud Run CPU, not on a GPU endpoint

- Status: accepted
- Date: 2026-11-20

## Context

The student is Qwen3-1.7B with a merged LoRA. Offline it was measured on an H200: in bf16
with vLLM, and as a Q4_K_M GGUF with llama.cpp's CUDA backend (`results/student_test.json`,
`results/student_gguf_test.json`). The deployed model has to run at a few PR events a day,
plus short load runs, and cost nothing when idle.

Options considered:
- **A GPU endpoint** (Vertex AI or Cloud Run with a GPU): fast prefill, but a GPU instance
  costs far more per second, and a model that stays loaded either keeps a GPU billed or pays a
  slow cold start.
- **The Q4_K_M GGUF (1.05 GiB) on llama.cpp's CPU server**, on Cloud Run with 4 vCPU,
  concurrency 1 (`-np 1`), a 16,384-token context, and request-based billing.

## Decision

CPU GGUF on Cloud Run. The model service has 4 vCPU and 12 GiB, and scales to zero (up to 9
instances for load runs).

## Evidence

- **Cost**, measured at list prices from Cloud Monitoring usage:
  - $0.028 per PR in the pilot (50 PRs, `results/pilot.json` @ bbcd83a);
  - $0.0285 per finished PR in the burst (168, `results/burst.json` @ 2b7ab61);
  - $0.0246 per PR in the shadow replay (103, `results/shadow.json` @ bd3590a).

  The model is 57–66% of it, and the worker (screening on CPU) almost all of the rest. The
  pre-pilot estimate was about 5 times lower: real diffs run up to about 6,000 tokens, and
  prefill on CPU dominates.
- **Quality is unchanged against the GPU measurement.** On the same 103 test PRs (all numpy,
  the repo held out of training), the deployed model's output matches maintainers on 57.7% of
  26 typed PRs, against 61.5% for the offline GGUF on the H200. Exact McNemar test: 1 vs 2
  discordant, p = 1.0 (`results/shadow_replay_model.json`,
  `results/student-gguf-system_test_shadow103.json` @ 23b8d5d). Valid output is 90.3% for
  both.
- **Latency:** worker start to stored triage, p50 86–96 s and p95 330–335 s (burst and shadow
  replay).

## Consequences

- **Memory:** at 8 GiB the model ran out of memory on long prompts in the burst (8.2–8.5 GiB
  used, 6 crashes), so it now has 12 GiB.
- **Slow prefill is the cost driver.** Shorter prompts (a tighter diff budget) or a smaller
  screening model would cut cost more than any instance size.
- A GPU endpoint was not priced or measured in this project, so no cost ratio against one is
  claimed.
- The CPU check covers 103 numpy PRs, not all four repos. The offline test numbers stay the
  model's quality figure.
