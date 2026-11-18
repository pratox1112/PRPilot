# ADR 0004: Plain async pipeline with its state in Postgres

- Status: accepted
- Date: 2026-11-17

## Context

A PR event has to go through screening, triage and a proposal, and the proposal then waits
for a human, sometimes for hours (roadmap §8.1). GitHub redelivers webhooks, and a push makes
a new head SHA, which needs a new triage. Only one step uses a model. Phase 6 runs the API
and the worker as separate Cloud Run services with Pub/Sub between them.

Options considered:
- **An agent graph or workflow engine** (Pydantic AI graphs, Temporal, Celery chains), with
  the wait for approval inside the run. That keeps a run open for hours, and the
  deterministic steps become graph nodes that are harder to test on their own.
- **Plain async Python with each step's state in Postgres**, ending the run at the stored
  proposal.

## Decision

`prpilot_core/pipeline.py` is ordinary async Python:
context → screen → [quarantined: stop] → triage → [fallback] → reviewers → comment → proposal.
Pydantic AI is used only for the triage call.

- One run per (repo, PR number, head SHA) (`uq_run_pr_sha`). The run row is read
  `FOR UPDATE`, so two deliveries of the same event cannot both triage it. A run that is
  `quarantined` or `proposed` is returned as it is. A `failed` run is tried again on the next
  delivery.
- The run ends at the stored `Proposal`. Approval starts the executor (ADR 0005); nothing
  waits inside the pipeline.
- Postgres 17 through SQLAlchemy 2 async (asyncpg) with Alembic migrations. CI checks that
  the migrations match the models (`alembic check`). The tests use a real Postgres: Docker
  locally, a service container in CI.
- The webhook handler only verifies the signature, records the delivery and puts the event
  on an `EventQueue`. Locally that is `InProcessQueue`; Phase 6 adds a Pub/Sub queue behind
  the same interface.

## Evidence

The pipeline tests (`tests/test_pipeline.py`, 94cf664) cover redelivery without a second
model call, a new push as a new run, a quarantined PR never reaching the model, and a failed
run being retried. The sandbox end-to-end run (PROGRESS 5.6) went webhook → queue →
`proposed`, and a second push made a second run.

## Consequences

- A step that fails rolls back on its own and marks the run `failed`, with the error stored.
  There are no half-written proposals.
- The pipeline and the queue are separate, so the Phase 6 change is in deployment, not in the
  pipeline.
- Tests need Postgres; without `PRPILOT_TEST_DATABASE_URL` they are skipped locally. CI
  always sets it.
