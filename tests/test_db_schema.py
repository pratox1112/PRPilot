"""The lock's database constraints hold in real Postgres (they are what fail closed)."""

import pytest
from sqlalchemy.exc import IntegrityError

from prpilot_core.db.models import Approval, Execution, Proposal, PRRun

pytestmark = pytest.mark.anyio


async def _proposal(session) -> Proposal:
    run = PRRun(repo="o/r", pr_number=1, head_sha="a" * 40, state="proposed")
    session.add(run)
    await session.flush()
    p = Proposal(run_id=run.id, repo="o/r", pr_number=1, head_sha="a" * 40,
                 content={"actions": []}, content_hash="sha256:" + "0" * 64)  # fmt: skip
    session.add(p)
    await session.flush()
    return p


async def test_one_run_per_pr_and_head_sha(db):
    async with db() as s:
        s.add(PRRun(repo="o/r", pr_number=1, head_sha="a" * 40))
        await s.commit()
        s.add(PRRun(repo="o/r", pr_number=1, head_sha="a" * 40))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_one_decision_per_action(db):
    async with db() as s:
        p = await _proposal(s)
        s.add(Approval(proposal_id=p.id, action_id="labels", decision="approve",
                       content_hash=p.content_hash, approver="me"))  # fmt: skip
        await s.commit()
        s.add(Approval(proposal_id=p.id, action_id="labels", decision="reject",
                       content_hash=p.content_hash, approver="me"))  # fmt: skip
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_an_approval_executes_once_and_an_action_succeeds_once(db):
    async with db() as s:
        p = await _proposal(s)
        a = Approval(proposal_id=p.id, action_id="labels", decision="approve",
                     content_hash=p.content_hash, approver="me")  # fmt: skip
        s.add(a)
        await s.flush()
        s.add(Execution(proposal_id=p.id, action_id="labels", approval_id=a.id, status="done"))
        await s.commit()
        s.add(Execution(proposal_id=p.id, action_id="labels", approval_id=a.id, status="done"))
        with pytest.raises(IntegrityError):
            await s.commit()
