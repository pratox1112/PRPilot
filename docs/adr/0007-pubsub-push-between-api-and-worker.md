# ADR 0007: Pub/Sub push between the api and the worker, not synchronous processing

- Status: accepted
- Date: 2026-11-20

## Context

GitHub expects a webhook to be answered within 10 seconds and redelivers when it is not. One
PR takes minutes on the deployed path: screening, then the student on Cloud Run CPU (median
about 1.5 minutes of worker time per PR, up to about 9 minutes; `results/burst.json`). A burst
of PR events is normal, for example after a release branch opens. ADR 0004 already ends the
pipeline at a stored proposal and puts events on an `EventQueue`.

Options considered:
- **Synchronous:** the api runs the pipeline inside the webhook request. Every request outlives
  GitHub's timeout, a redelivery starts a second run, and a burst needs one api instance per
  PR in progress.
- **Pub/Sub pull:** a long-running worker pulls messages. That needs an always-on instance,
  which a scale-to-zero Cloud Run service does not have.
- **Pub/Sub push** to the worker's `/pubsub` endpoint, with retries, backoff and a dead-letter
  topic.

## Decision

The api checks the signature, stores the delivery id (a unique row), publishes the event to
`prpilot-pr-events` and answers 202. The subscription pushes to the worker, which has internal
ingress and only accepts the push identity.

- Ack deadline 600 s (the most a push subscription allows); backoff 10–600 s.
- After `max_delivery_attempts` (5 by default) a message goes to the dead-letter topic. A hold
  subscription keeps it for 7 days, and an alert fires when it is not empty.
- Duplicates are handled twice: a repeated delivery id is dropped at the api, and the worker
  locks one run per (repo, PR, head SHA), so a redelivered message never makes a second
  proposal (ADR 0004).
- Load runs raise the attempts (`-var max_delivery_attempts=20`) together with capacity.

## Evidence

- Pilot, 50 PRs at 3 in flight (`results/pilot.json` @ bbcd83a): 50 proposed, nothing
  dead-lettered.
- Burst, 500 PRs at once plus 50 redeliveries against 10 workers (`results/burst.json` @
  2b7ab61):
  - all 50 redeliveries answered `duplicate`;
  - 0 duplicate proposals (168 PRs, 168 proposals);
  - 0 lost: each of the 332 unfinished PRs is held in the dead-letter subscription (read
    without consuming it).
- Shadow replay, 103 PRs at 8 in flight (`results/shadow.json` @ bd3590a): 0 lost, 0
  duplicates, nothing dead-lettered.

## Consequences

- **Push retries count as delivery attempts even when no worker is free.** With concurrency 1,
  a busy worker service answers 429, and Pub/Sub counts that as a failed delivery. In the
  burst, events used up 20 attempts in about 1 h 45 min (8,273 `unreachable` answers) while
  the backlog needed about 5 hours. The queue protects the system and loses nothing, but a
  burst larger than capacity × retry window ends in the dead-letter queue, not in a late
  triage. Fixes, not built: a pull worker, more capacity, or a longer retry window (Pub/Sub
  allows up to 100 attempts).
- **A delivery that outlives the 600 s ack deadline is pushed again** while the first is still
  running. The run lock keeps this from making a duplicate, but the second push holds another
  worker while it waits on the lock. In the burst, 31 deliveries exceeded the deadline, and 23
  PRs finished and were also dead-lettered. In the shadow replay, 5 exceeded it.
- Redriving the dead-letter queue (republishing its messages to the topic) is the recovery
  path. It was not exercised.
