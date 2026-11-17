"""The approval lock (roadmap §8.3): every refusal the roadmap lists, in real Postgres."""

import json

import pytest
from sqlalchemy import func, select

from prpilot_core.db.models import Execution, Proposal, PRRun, Refusal
from prpilot_core.executor import Refused, approve, execute
from prpilot_core.proposals import build_content, content_hash
from prpilot_core.schemas import TriageOutput

pytestmark = pytest.mark.anyio
SANDBOX = frozenset({"me/sandbox"})
TRIAGE = TriageOutput.model_validate_json(
    json.dumps(
        {"summary": "Fixes a crash.", "type": "bug", "risk": "low", "labels": ["kind/bug"],
         "reviewers": ["alice"], "related_issues": [], "rationale": "Small fix."}
    )
)  # fmt: skip


class FakeWriter:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def add_labels(self, repo, pr, labels):
        return self._call("add_labels", repo, pr, labels)

    async def request_reviewers(self, repo, pr, logins):
        return self._call("request_reviewers", repo, pr, logins)

    async def post_comment(self, repo, pr, body):
        return self._call("post_comment", repo, pr, body)

    def _call(self, *args):
        if self.fail:
            raise RuntimeError("GitHub said no")
        self.calls.append(args)
        return {"ok": True}


async def make_proposal(s, repo="me/sandbox") -> Proposal:
    run = PRRun(repo=repo, pr_number=7, head_sha="b" * 40, state="proposed")
    s.add(run)
    await s.flush()
    content = build_content(repo, 7, "b" * 40, TRIAGE, "Triage summary for review.")
    p = Proposal(run_id=run.id, repo=repo, pr_number=7, head_sha="b" * 40, content=content,
                 content_hash=content_hash(content))  # fmt: skip
    s.add(p)
    await s.commit()
    return p


async def refusals(s) -> list[str]:
    return list(await s.scalars(select(Refusal.reason).order_by(Refusal.id)))


async def test_approved_action_runs_once(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert out.ok and w.calls == [("add_labels", "me/sandbox", 7, ["kind/bug"])]


async def test_unapproved_proposal_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert not out.ok and out.reason == "unapproved" and not w.calls
        assert await refusals(s) == ["unapproved"]


async def test_rejected_action_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "comment", p.content_hash, "owner", "reject")
        out = await execute(s, p.id, "comment", FakeWriter(), SANDBOX)
        assert out.reason == "rejected"


async def test_tampered_proposal_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        # edit the stored content after approval, keeping the old hash
        content = {**p.content, "actions": [{**p.content["actions"][0], "labels": ["merge-me"]}]}
        p.content = content
        await s.commit()
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert out.reason == "tampered" and not w.calls


async def test_tampered_and_rehashed_proposal_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        # an attacker who can write the table edits the content and recomputes its hash
        content = {**p.content, "actions": [{**p.content["actions"][0], "labels": ["merge-me"]}]}
        p.content, p.content_hash = content, content_hash(content)
        await s.commit()
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert out.reason == "hash_mismatch" and not w.calls


async def test_non_allowlisted_repo_is_refused(db):
    async with db() as s:
        p = await make_proposal(s, repo="kubernetes/kubernetes")
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert out.reason == "not_allowlisted" and not w.calls


async def test_replayed_approval_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        w = FakeWriter()
        assert (await execute(s, p.id, "labels", w, SANDBOX)).ok
        out = await execute(s, p.id, "labels", w, SANDBOX)
        assert out.reason == "replayed" and len(w.calls) == 1
        n = await s.scalar(select(func.count()).select_from(Execution))
        assert n == 1


async def test_partial_approval_runs_only_the_approved_action(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        await approve(s, p.id, "comment", p.content_hash, "owner", "reject")
        w = FakeWriter()
        results = {a: (await execute(s, p.id, a, w, SANDBOX)).reason
                   for a in ("labels", "reviewers", "comment")}  # fmt: skip
        assert results == {"labels": "done", "reviewers": "unapproved", "comment": "rejected"}
        assert [c[0] for c in w.calls] == ["add_labels"]


async def test_approving_stale_content_or_twice_is_refused(db):
    async with db() as s:
        p = await make_proposal(s)
        with pytest.raises(Refused, match="stale_or_tampered"):
            await approve(s, p.id, "labels", "sha256:" + "f" * 64, "owner", "approve")
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        with pytest.raises(Refused, match="already_decided"):
            await approve(s, p.id, "labels", p.content_hash, "owner", "reject")


async def test_failed_write_is_recorded_and_not_retried(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        out = await execute(s, p.id, "labels", FakeWriter(fail=True), SANDBOX)
        assert out.reason == "error" and "GitHub said no" in out.detail["error"]
        assert (await execute(s, p.id, "labels", FakeWriter(), SANDBOX)).reason == "replayed"


async def test_dry_run_writes_nothing(db):
    async with db() as s:
        p = await make_proposal(s)
        await approve(s, p.id, "labels", p.content_hash, "owner", "approve")
        w = FakeWriter()
        out = await execute(s, p.id, "labels", w, SANDBOX, dry_run=True)
        assert out.ok and out.detail["dry_run"] and not w.calls
