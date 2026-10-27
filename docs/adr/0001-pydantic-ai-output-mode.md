# ADR 0001: Pydantic AI output mode for the triage agent

- Status: accepted (feasibility check passed: `results/feasibility_pydantic_ai.json` @ b36d622)
- Date: 2026-10-24

## Context

The triage step runs a small fine-tuned Qwen3-1.7B served by llama.cpp's OpenAI-compatible
server. The student is trained to emit JSON **text**, not tool calls, and the training prompt
must be byte-for-byte what the agent sends at inference (roadmap §6.1). We need typed output,
repo-specific validation with retries, and a clean way to measure first-attempt validity
separately from validity after retries (hard rule 10).

Pinned version: `pydantic-ai-slim[openai]==2.61.0` (Python 3.12).

Output modes in this version (`pydantic_ai.output.OutputMode`): `text`, `tool`, `native`,
`prompted`, `image`, `auto` (`tool_or_text` is deprecated). Structured modes are wrapped by
`ToolOutput`, `NativeOutput` and `PromptedOutput`; plain text is handled by `TextOutput`.

## Decision

Use **`TextOutput(parse_triage_output)`** with our own system prompt that embeds the JSON
Schema of `TriageOutput`.

- `parse_triage_output(ctx: RunContext[TriageDeps], text: str)` parses the reply with
  `TriageOutput.model_validate_json` (static schema), then checks repo rules from `ctx.deps`
  (labels exist, reviewers eligible and not the author, issues exist). Any failure raises
  `ModelRetry` with a precise message, which Pydantic AI sends back as a `RetryFeedbackPart`.
- Retry budget: `Agent(retries={"output": 2})` (`AgentRetries`; on the text path this is a
  per-run budget). Recorded in every results file that reports system metrics.
- Model: `OpenAIChatModel(<name>, provider=OpenAIProvider(base_url=..., api_key=...))`.
- Settings: `ModelSettings(temperature=0.0, seed=0, max_tokens=1024,
  extra_body={"chat_template_kwargs": {"enable_thinking": False}})`. The llama.cpp server runs
  with `--jinja` so the model's chat template honours `enable_thinking`.
- Tracing: `Agent.instrument_all(InstrumentationSettings(tracer_provider=...))` with an OTLP
  HTTP exporter to Langfuse (`{LANGFUSE_HOST}/api/public/otel/v1/traces`, Basic auth).
- No tools are registered on the agent (verified by unit test).

## Why not the alternatives

- **`PromptedOutput`** injects its own instruction template into the prompt. That text would
  have to be reproduced exactly in the training data and could change between library
  versions, silently breaking the train/inference format match.
- **`ToolOutput`** (the default for structured types) asks the model for a tool call. The
  student is trained to emit JSON text, and a 1.7B model is unreliable at tool calling.
- **`NativeOutput`** relies on server-side JSON-schema enforcement. That is constrained
  decoding: allowed in production later, but it must never feed the model's own validity
  metric (hard rule 10), and it hides the failures we want to measure.

With `TextOutput`, Pydantic AI adds nothing to the prompt (verified: no extra instructions,
no tools), so `SYSTEM_PROMPT` and `build_user_prompt` in `prpilot_core/agents/triage.py` are
the whole prompt and can be reused verbatim to build training examples.

## Measuring validity

- Model metric: the first `ModelResponse` text is validated once with `parse_static`, outside
  the retry loop (captured via `capture_run_messages`).
- System metric: whether `agent.run_sync` returns a valid `TriageOutput` within the retry
  budget.

## Consequences

- The JSON Schema text in `SYSTEM_PROMPT` is part of the training format; changing the schema
  changes the prompt, so training data must be rebuilt.
- Reply must be raw JSON (no code fences). This is strict on purpose; if the base model
  often adds fences, that is a finding to record, not something to hide by lenient parsing.
