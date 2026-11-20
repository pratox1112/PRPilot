# ADR 0008: Cloud Run, not GKE

- Status: accepted
- Date: 2026-11-20

## Context

PRPilot runs four services (api, worker, executor, model), a database and a queue. Traffic
is bursty and mostly idle: a few PR events a day on the sandbox repo, and load runs that last
a few hours. The deployment exists to build, measure and record a demo, then is torn down to
$0 (roadmap §11a). The owner's BrainCT project already runs on GKE, so GKE was a real option
and not an unknown.

Options considered:
- **GKE (Standard or Autopilot):** full control over scheduling, node types and long-running
  pull workers. A cluster has a management fee ($0.10 an hour at list price; waived for one
  zonal or Autopilot cluster per billing account), Standard nodes bill while they run, and the
  cluster, node pools, ingress and autoscaling all need managing.
- **Cloud Run:** each service is a container with request-based billing that scales to zero,
  with IAM-checked invocation between services and push targets for Pub/Sub.

## Decision

Cloud Run for all four services, plus a Cloud Run job for the migrations. All of it is in
Terraform (`infra/terraform/`), and CI runs fmt, validate and plan.

- Request-based billing (`cpu_idle`): an idle service costs nothing.
- Concurrency 1 for the worker and the model, so a PR never shares a model instance and gets
  its whole context.
- Service-to-service calls use Cloud Run IAM: only the push identity may call the worker, only
  the worker may call the model, and only the owner's account may call the executor.
- Capacity is a variable (`max_instances`, `model_min_instances`), raised only for load runs.

## Evidence

- Measured serving cost at list prices: $0.028 per PR in the pilot, $0.0285 in the burst and
  $0.0246 in the shadow replay (`results/pilot.json`, `burst.json`, `shadow.json`). Idle time
  between runs cost only the database, and that is paused between sessions.
- The burst ran 10 worker and 10 model instances with no cluster set up.

## Consequences

- **The region's memory quota limits scale.** One service may reserve at most 112 GiB in
  us-east1. Ten model instances at 16 GiB were refused, so the model runs at 12 GiB with up
  to 9 instances (ac97661). A larger load test needs a quota increase or another region.
- **Cold starts:** a new worker loads its screening models for about 70 s before it takes
  traffic, and a new model instance loads the GGUF. Load runs set `model_min_instances=1`.
- No long-running pull consumer: the queue has to push (ADR 0007), with the retry behaviour
  that brings.
- A few Cloud Run behaviours had to be worked around: `/healthz` is reserved by the front end,
  and a revision that failed for a missing image is not retried (both in
  `docs/runbooks/gcp-deploy.md`).
