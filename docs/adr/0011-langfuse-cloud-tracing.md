# ADR 0011: Langfuse Cloud for traces, Cloud Monitoring for the system

- Status: accepted
- Date: 2026-11-20

## Context

Roadmap §9.5 asks for one trace per PR, with a span per pipeline step and the model's tokens
and latency, and for system metrics with a dead-letter alert. Traces may contain only
screened (redacted) text. The deployment is torn down after the demo, and its traces should
survive the teardown.

Options considered:
- **Self-hosted Langfuse** on Cloud Run with its own database: another service to run, pay
  for and tear down, and the traces go with it.
- **Langfuse Cloud, free tier:** an OpenTelemetry endpoint and two keys, and the traces stay
  after teardown.
- **Cloud Trace only:** no LLM view (prompts, completions, tokens per call).

## Decision

Langfuse Cloud through Pydantic AI's OpenTelemetry instrumentation (`prpilot_core/tracing.py`):

- One `pr_run` trace per PR, with spans `context`, `screen`, `triage` (the model calls nested
  inside) and `proposal`. Span attributes hold ids, scores and states only. PR text appears
  only in the model spans, after screening.
- Off unless `TRACING=true`. Startup fails if it is on without the keys. The keys are Secret
  Manager secrets mounted into the worker only. Terraform's `-var tracing=true` turns it on.
- Cloud Monitoring keeps the system view: a dashboard with p95 request latency, requests by
  status class, Pub/Sub backlog, dead-lettered events and instances per service, and an email
  alert when the dead-letter subscription is not empty.

## Evidence

- `tests/test_tracing.py` (4 tests): off by default; when on, agent and model spans are
  exported; the keys are required; the endpoint and auth are correct.
- Deployed: the owner checked in Langfuse that the trace count matched the replayed runs
  (2026-11-20).
- The burst met the alert's condition: 355 events were held in the dead-letter subscription.
  Whether the email arrived was not recorded.

## Consequences

- Traces leave GCP for a third party. Only screened text goes there, but the free tier's
  retention and limits apply.
- Billing and usage numbers come from Cloud Monitoring and the price catalog
  (`replay.measure`), not from Langfuse's cost view.
