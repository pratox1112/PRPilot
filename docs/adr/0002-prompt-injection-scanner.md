# ADR 0002: Prompt-injection scanner — PromptGuard 2 directly, not through LlamaFirewall

- Status: accepted
- Date: 2026-10-26

## Context

The roadmap planned LlamaFirewall's PromptGuard scanner as the injection screen (§7.1).
Checking `llamafirewall==1.0.3` against our pinned stack found three problems:

1. **It does not import.** `scanners/promptguard_utils.py` imports `HfFolder` from
   `huggingface_hub`, which was removed in `huggingface_hub` 1.0. Current `transformers`
   (5.20.2) requires `huggingface_hub>=1.31`, so the scanner fails on import.
2. **It truncates at 512 tokens.** The text is tokenized with `truncation=True,
   max_length=512` and scored once. PR diffs are far longer, so a payload placed after the
   first ~512 tokens is never scanned.
3. **The model is not pinned.** It downloads `meta-llama/Llama-Prompt-Guard-2-86M` at the
   latest revision, which breaks hard rule 8.

The scanner itself is a thin wrapper: PromptGuard 2 classifier + softmax + threshold 0.9.

## Decision

Use PromptGuard 2 directly with `transformers` (the documented 0.7 fallback), in
`prpilot_core/screening/prompt_guard.py`:

- models pinned by revision: 86M @ `a8ded8e697ce7c355e395a0df51f94adb4a2fd27`,
  22M @ `11614a155199674a0a95e6602d6ab0417b790ed0`, downloaded with `snapshot_download`;
- the whole text is split into overlapping windows of 512 tokens (special tokens included),
  stride 256, and scored by its **maximum** window score;
- block threshold 0.9 (LlamaFirewall's default), to be tuned on the validation split only;
- torch / transformers live in the `screening` dependency group and are imported lazily.

## Consequences

- Wording in the resume entry becomes "PromptGuard 2", not "LlamaFirewall (PromptGuard 2)".
- Max-over-windows raises recall on long inputs but also gives more chances for a false
  positive; the clean-control false-positive rate (§7.2) measures that cost.
- LlamaFirewall's whitespace preprocessing is not reproduced; revisit if the red-team set
  shows whitespace-based evasion.
- PromptGuard 2 is under Meta's Llama license; we use it as a classifier only and do not
  train on or redistribute it.
