# ADR 0006: Deterministic context prefetch; no LLM issue-search tool

- Status: accepted
- Date: 2026-11-17

## Context

The triage prompt shows the PR, its diff and file list, and three candidate lists: labels,
eligible reviewers and open issues. The roadmap (§8.2) allows registering `search_issues` as a
Pydantic AI tool for finding related issues. If that is done, issue recall@5 must be compared
between tool calling and a deterministic search over the same index, and the winner kept.

## Decision

Everything is gathered before the model call (`prpilot_core/context.py`), built exactly as the
training prompts were:
- the diff is fitted to 6,000 tokens with the student's tokenizer;
- labels are filtered by usage stats;
- reviewers are ranked from path history (the Phase 2 formula);
- open issues are ranked by the Phase 2 BM25 scorer, with the issues the PR names first.

A replayed PR uses its stored open-time candidate lists as they are.

**No tool is registered, and the tool-calling comparison was not run** (owner, 2026-11-17).

## Why

- The student was fine-tuned on prompts that already contain the issue list (sft-v1). A tool
  would change its input from what it was trained on, and it was never trained to call tools.
  A 1.7B model is also unreliable at tool calling (ADR 0001).
- The deterministic list already works. Student issue@5 on the test split is 0.883 with these
  candidates (PROGRESS 3.6).
- A fair comparison would need tool-calling training data and a second fine-tune. That costs
  more than the open question is worth at this stage.

## Consequences

- Training and serving use the same prompt builder (`prpilot_core.agents.triage_format`) and
  the same candidate code (`prpilot_data.candidates`). A test checks that the live reviewer
  ranking equals Phase 2's.
- The agent stays tool-free, which also keeps it out of reach for writes (ADR 0005).
- Issues the BM25 ranking misses cannot be found by the model. If issue recall becomes the
  weak point, the comparison above is the next experiment.
