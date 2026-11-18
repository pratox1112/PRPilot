# ADR 0005: The model proposes, a separate executor writes after approval

- Status: accepted
- Date: 2026-11-17

## Context

The bot writes to GitHub: labels, review requests and a comment. A PR's title, body and diff
are untrusted input (Phase 4), so a model that has read them must not be able to write
anything on its own. The roadmap's lock (§8.3) requires proving that each of these is
refused: an unapproved proposal, a tampered proposal, a repo that is not allowlisted, and a
replayed approval.

## Decision

- **No write tools on the agent.** The triage agent has no tools at all (ADR 0001). The
  write methods exist only on the executor's `GitHubWriter`.
- **Proposals are stored with a hash.** Each proposal holds its actions (`labels`,
  `reviewers`, `comment`) and the SHA-256 of their canonical JSON (`prpilot_core/proposals.py`).
- **Each action is approved on its own,** bound to the proposal and the hash the approver saw.
  The approver may approve the labels and reject the comment. There is one decision per
  action (`uq_approval_action`).
- **`execute()` checks in order and fails closed.** The proposal must exist and its stored
  content must still hash to its stored hash. The action must exist. It must be approved, not
  rejected, and approved for the current hash. The repo must be on `SANDBOX_REPOS`. The action
  must not have run before. The execution row is claimed (`uq_execution_approval`) **before**
  the write. Every refusal is logged in `refusals` with its reason.
- **Approvals use a static admin token** for now (`ADMIN_TOKEN`, a bearer header).
- **Dry run is the default.** `EXECUTOR_DRY_RUN=true` records the execution and writes
  nothing.
- A successful real write feeds memory: the applied labels count toward label stats, and the
  requested reviewers toward reviewer history.

## Evidence

`tests/test_lock.py` (11 tests, cc30cbe) refuses an unapproved proposal, a rejected action, a
tampered proposal (also when the attacker re-hashes it), an approval for a hash other than the
stored one, a non-allowlisted repo, and a replay (one write, one execution row). It also
covers partial approval, a failed write that is recorded and not retried, and a dry run that
writes nothing. Two simultaneous executes are stopped by the unique execution row, but no test
runs them at the same time. On the sandbox repo (PROGRESS 5.6), a dry run wrote nothing. The real run wrote the
approved label and comment as the GitHub App, and executing the same action again was refused
with 409 `replayed`.

## Consequences

- An injected PR can at worst get a bad proposal shown to a human. It cannot write.
- A proposal can no longer be edited after it is stored. A changed proposal is a new one, with
  a new hash and new approvals.
- The admin token is one shared secret with no per-user identity. It is replaced by GitHub
  OAuth with the dashboard (roadmap Phase 7).
