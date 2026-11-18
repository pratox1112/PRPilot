"""The API (FastAPI): GitHub webhook receiver + the approval API the dashboard calls.

Webhook (`POST /webhook`): verify the HMAC signature, drop a delivery seen before (by
X-GitHub-Delivery, a unique key in Postgres), publish the PR to the event queue, answer 202 at
once. All heavy work happens in the worker.

Approval API (admin token, `Authorization: Bearer ...`):
- `GET  /runs`, `GET /proposals`, `GET /proposals/{id}`: what the bot did and proposes,
  including quarantined PRs;
- `POST /proposals/{id}/actions/{action}/decision` {decision, content_hash}: approve or reject
  one action, bound to the content hash the human was shown;
- `POST /proposals/{id}/actions/{action}/execute`: run an approved action through the
  executor (all lock checks), then record the outcome in memory.
Refusals come back as 409 with the reason; nothing is written to GitHub for them.
"""

import hmac
import json
import uuid
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from prpilot_core import memory
from prpilot_core.db.models import Approval, Delivery, Execution, Proposal, PRRun
from prpilot_core.executor import GitHubWriter, Refused, approve, execute
from prpilot_core.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    verify_signature,
)
from prpilot_core.queue import EventQueue

PR_ACTIONS = ("opened", "reopened", "synchronize", "ready_for_review")


@dataclass
class Services:
    sessions: object  # async_sessionmaker
    queue: EventQueue
    webhook_secret: str
    admin_token: str
    writer: GitHubWriter | None
    allowlist: frozenset[str]
    dry_run: bool = True


class Decision(BaseModel):
    decision: str  # approve | reject
    content_hash: str


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="PRPilot API")
    app.state.services = services

    def admin(authorization: str = Header(default="")) -> str:
        token = services.admin_token
        if not token:
            raise HTTPException(503, "approval API disabled: ADMIN_TOKEN is not set")
        if not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "bad or missing admin token")
        return "admin"

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/webhook", status_code=202)
    async def webhook(request: Request) -> dict:
        body = await request.body()
        if not verify_signature(
            services.webhook_secret, body, request.headers.get(SIGNATURE_HEADER)
        ):
            raise HTTPException(401, "bad or missing signature")
        delivery = request.headers.get(DELIVERY_HEADER)
        event = request.headers.get(EVENT_HEADER, "")
        if not delivery:
            raise HTTPException(400, "missing delivery id")
        payload = json.loads(body)
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        async with services.sessions() as s:
            fresh = await s.scalar(
                insert(Delivery)
                .values(delivery_id=delivery, event=event, repo=repo, pr_number=pr.get("number"))
                .on_conflict_do_nothing()
                .returning(Delivery.delivery_id)
            )
            await s.commit()
        if fresh is None:
            return {"status": "duplicate"}
        if event != "pull_request" or payload.get("action") not in PR_ACTIONS or pr.get("draft"):
            return {"status": "ignored"}
        await services.queue.publish({
            "repo": repo, "number": pr["number"], "delivery_id": delivery,
            "installation_id": (payload.get("installation") or {}).get("id"),
        })  # fmt: skip
        return {"status": "queued"}

    @app.get("/runs")
    async def runs(state: str | None = None, who: str = Depends(admin)) -> list[dict]:
        async with services.sessions() as s:
            q = select(PRRun).order_by(PRRun.id.desc()).limit(200)
            if state:
                q = q.where(PRRun.state == state)
            return [{"id": r.id, "repo": r.repo, "pr_number": r.pr_number, "head_sha": r.head_sha,
                     "state": r.state, "screen": r.screen, "error": r.error}
                    for r in await s.scalars(q)]  # fmt: skip

    async def describe(s, p: Proposal) -> dict:
        approvals = {a.action_id: a for a in await s.scalars(
            select(Approval).where(Approval.proposal_id == p.id))}  # fmt: skip
        execs = {e.action_id: e for e in await s.scalars(
            select(Execution).where(Execution.proposal_id == p.id))}  # fmt: skip
        actions = []
        for a in p.content["actions"]:
            dec, ex = approvals.get(a["id"]), execs.get(a["id"])
            actions.append({**a, "decision": dec.decision if dec else None,
                            "execution": ex.status if ex else None})  # fmt: skip
        return {"id": str(p.id), "repo": p.repo, "pr_number": p.pr_number,
                "head_sha": p.head_sha, "content_hash": p.content_hash, "actions": actions,
                "triage": p.content["triage"], "notes": p.content["notes"]}  # fmt: skip

    @app.get("/proposals")
    async def proposals(who: str = Depends(admin)) -> list[dict]:
        async with services.sessions() as s:
            ps = await s.scalars(select(Proposal).order_by(Proposal.created_at.desc()).limit(200))
            return [await describe(s, p) for p in ps]

    def parse_id(proposal_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(proposal_id)
        except ValueError as e:
            raise HTTPException(404, "no such proposal") from e

    @app.get("/proposals/{proposal_id}")
    async def proposal(proposal_id: str, who: str = Depends(admin)) -> dict:
        async with services.sessions() as s:
            p = await s.get(Proposal, parse_id(proposal_id))
            if p is None:
                raise HTTPException(404, "no such proposal")
            return await describe(s, p)

    @app.post("/proposals/{proposal_id}/actions/{action_id}/decision")
    async def decide(proposal_id: str, action_id: str, body: Decision,
                     who: str = Depends(admin)) -> dict:  # fmt: skip
        async with services.sessions() as s:
            try:
                a = await approve(s, parse_id(proposal_id), action_id, body.content_hash, who,
                                  body.decision)  # fmt: skip
            except Refused as e:
                raise HTTPException(409, {"refused": e.reason, "detail": e.detail}) from e
            return {"approval_id": str(a.id), "decision": a.decision}

    @app.post("/proposals/{proposal_id}/actions/{action_id}/execute")
    async def run_action(proposal_id: str, action_id: str, who: str = Depends(admin)) -> dict:
        pid = parse_id(proposal_id)
        async with services.sessions() as s:
            out = await execute(s, pid, action_id, services.writer, services.allowlist,
                                dry_run=services.dry_run)  # fmt: skip
            if not out.ok:
                raise HTTPException(409, {"refused": out.reason, "detail": out.detail})
            if not services.dry_run:
                p = await s.get(Proposal, pid)
                act = next(a for a in p.content["actions"] if a["id"] == action_id)
                if act["type"] == "request_reviewers":
                    files = p.content["notes"].get("changed_files", [])
                    await memory.record_review(s, p.repo, files, act["reviewers"])
                elif act["type"] == "add_labels":
                    await memory.record_labels(s, p.repo, act["labels"])
                await s.commit()
            return {"status": "done", "dry_run": services.dry_run, "detail": out.detail}

    return app
