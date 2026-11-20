# ADR 0010: Pydantic AI for the triage call, not LangGraph

- Status: accepted
- Date: 2026-11-20

## Context

The only model step in PRPilot is one triage call. A 1.7B student must return a
`TriageOutput` that passes repo rules: labels that exist, eligible reviewers, open issues.
When it does not, the error should go back to the model a bounded number of times. The
owner's NL2SQL project already uses LangGraph, so this choice was made knowingly.

Options considered:
- **LangGraph:** a graph of nodes with state, good for multi-step agents with branches and
  tools. Typed output and validator retries would be built by hand as nodes and edges.
- **Pydantic AI:** an agent with a typed output, validators that raise `ModelRetry`, a retry
  budget, and OpenTelemetry instrumentation built in.

## Decision

Pydantic AI 2.61 for the triage call only, with `TextOutput(parse_triage_output)` and 2
output retries (ADR 0001). Everything around the call (context, screening, fallback,
reviewers, proposal) is plain async Python with its state in Postgres (ADR 0004). There is no
graph framework.

## Evidence

- The feasibility check: 3/3 valid on the first attempt, with traces in Langfuse
  (`results/feasibility_pydantic_ai.json` @ b36d622).
- The retries are measured separately from first-attempt validity (hard rule 10), with the
  same agent offline and deployed. On the test split, the Q4_K_M student is valid on 75.9% with
  retries (`results/student_gguf_test.json` @ 3baac29). In the shadow replay, 44 of 103 numpy
  PRs were valid after retries, and the fallback filter rescued 49 more
  (`results/shadow.json` @ bd3590a).
- The deployed traces come from Pydantic AI's own instrumentation, with the model calls
  nested under the pipeline's `triage` span (ADR 0011).

## Consequences

- A multi-step agent (for example an LLM issue-search tool) would need its own design. It was
  not built (ADR 0006).
- Retries are per run, so a PR whose output stays invalid costs up to 3 model calls. In the
  shadow replay that was 2.17 calls per PR on average.
