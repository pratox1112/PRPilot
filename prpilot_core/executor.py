"""The executor: the only code that writes to GitHub, and only for approved actions.

It runs one action of one proposal, and fails closed (refuses, logs why, writes nothing)
unless all of these hold, checked in this order:
1. the proposal exists and its stored content still matches its stored hash (not edited);
2. the action exists in that content;
3. a human approved this action (an approval, not a rejection);
4. the approval was given for exactly this content (its hash matches);
5. the target repo is on the sandbox allowlist;
6. this approval has not been used before (also enforced by a unique constraint, so two
   executors racing on one approval cannot both write).
A write that fails is recorded as an error and uses up its approval: it is never retried
silently (a retry is a deliberate operator decision). Write actions are never registered as
model tools; the triage agent cannot reach this code.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from prpilot_core.db.models import Approval, Execution, Proposal, Refusal
from prpilot_core.proposals import action, content_hash


class GitHubWriter(Protocol):
    async def add_labels(self, repo: str, pr_number: int, labels: list[str]) -> dict: ...
    async def request_reviewers(self, repo: str, pr_number: int, logins: list[str]) -> dict: ...
    async def post_comment(self, repo: str, pr_number: int, body: str) -> dict: ...


@dataclass(frozen=True)
class Outcome:
    ok: bool
    reason: str  # "done" or the refusal reason
    detail: dict | None = None


class Refused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


async def _refuse(
    session, proposal_id, action_id, reason: str, detail: str = "", failed_write: bool = False
) -> Outcome:
    if failed_write:  # only after a failed insert; a rollback expires every loaded object
        await session.rollback()
    session.add(Refusal(proposal_id=proposal_id, action_id=action_id, reason=reason, detail=detail))
    await session.commit()
    return Outcome(False, reason)


async def approve(
    session, proposal_id: uuid.UUID, action_id: str, seen_hash: str, approver: str, decision: str
) -> Approval:
    """Record a human decision on one action. `seen_hash` is the hash of the content the
    human was shown; if the proposal changed since, the decision is refused."""
    if decision not in ("approve", "reject"):
        raise Refused("bad_decision", decision)
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None:
        raise Refused("unknown_proposal")
    if action(proposal.content, action_id) is None:
        raise Refused("unknown_action", action_id)
    if seen_hash != proposal.content_hash or content_hash(proposal.content) != seen_hash:
        raise Refused("stale_or_tampered", "the proposal changed since it was shown")
    approval = Approval(proposal_id=proposal_id, action_id=action_id, decision=decision,
                        content_hash=seen_hash, approver=approver)  # fmt: skip
    session.add(approval)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise Refused("already_decided", action_id) from e
    return approval


async def execute(
    session,
    proposal_id: uuid.UUID,
    action_id: str,
    writer: GitHubWriter,
    allowlist: frozenset[str],
    dry_run: bool = False,
) -> Outcome:
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None:
        return await _refuse(session, proposal_id, action_id, "unknown_proposal")
    if content_hash(proposal.content) != proposal.content_hash:
        return await _refuse(session, proposal_id, action_id, "tampered", "content != stored hash")
    act = action(proposal.content, action_id)
    if act is None:
        return await _refuse(session, proposal_id, action_id, "unknown_action")
    approval = await session.scalar(
        select(Approval).where(Approval.proposal_id == proposal_id, Approval.action_id == action_id)
    )
    if approval is None:
        return await _refuse(session, proposal_id, action_id, "unapproved")
    if approval.decision != "approve":
        return await _refuse(session, proposal_id, action_id, "rejected")
    if approval.content_hash != proposal.content_hash:
        return await _refuse(session, proposal_id, action_id, "hash_mismatch",
                             "approved content differs from the proposal")  # fmt: skip
    repo = proposal.content["repo"]
    if repo != proposal.repo or repo not in allowlist:
        return await _refuse(session, proposal_id, action_id, "not_allowlisted", repo)

    # Claim the approval first: the unique constraint makes a replay or a race fail here.
    run = Execution(proposal_id=proposal_id, action_id=action_id, approval_id=approval.id,
                    status="running", dry_run=dry_run)  # fmt: skip
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        return await _refuse(session, proposal_id, action_id, "replayed", failed_write=True)

    pr = proposal.content["pr_number"]
    try:
        if dry_run:
            detail = {"dry_run": True, "action": act}
        elif act["type"] == "add_labels":
            detail = await writer.add_labels(repo, pr, act["labels"])
        elif act["type"] == "request_reviewers":
            detail = await writer.request_reviewers(repo, pr, act["reviewers"])
        elif act["type"] == "post_comment":
            detail = await writer.post_comment(repo, pr, act["body"])
        else:
            raise ValueError(f"unknown action type {act['type']}")
    except Exception as e:  # recorded, never retried silently
        run.status, run.detail = "error", {"error": f"{type(e).__name__}: {e}"}
        await session.commit()
        return Outcome(False, "error", run.detail)
    run.status, run.detail = "done", detail
    await session.commit()
    return Outcome(True, "done", detail)
