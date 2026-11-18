"""The API: webhook signature + dedupe + queueing, and the approval flow through the lock."""

import json

import httpx
import pytest
from sqlalchemy import select

from prpilot_core.db.models import LabelStat, Proposal, PRRun, ReviewerHistory
from prpilot_core.github.webhooks import sign
from prpilot_core.proposals import build_content, content_hash
from prpilot_core.schemas import TriageOutput
from services.api.app import Services, create_app

pytestmark = pytest.mark.anyio
SECRET, TOKEN = "whsec", "admintok"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class ListQueue:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakeWriter:
    def __init__(self):
        self.calls = []

    async def add_labels(self, repo, pr, labels):
        self.calls.append(("labels", labels))
        return {"labels": labels}

    async def request_reviewers(self, repo, pr, logins):
        self.calls.append(("reviewers", logins))
        return {"requested": logins}

    async def post_comment(self, repo, pr, body):
        self.calls.append(("comment", body))
        return {"comment_id": 1}


def client(db, token=TOKEN, **over):
    svc = Services(
        sessions=db,
        queue=ListQueue(),
        webhook_secret=SECRET,
        admin_token=token,
        writer=FakeWriter(),
        allowlist=frozenset({"me/sandbox"}),
        dry_run=False,
    )
    for k, v in over.items():
        setattr(svc, k, v)
    app = create_app(svc)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t"), svc


def hook(payload, delivery="d-1", event="pull_request", secret=SECRET):
    body = json.dumps(payload).encode()
    return {
        "content": body,
        "headers": {
            "X-Hub-Signature-256": sign(secret, body),
            "X-GitHub-Delivery": delivery,
            "X-GitHub-Event": event,
        },
    }


PR_OPENED = {"action": "opened", "repository": {"full_name": "me/sandbox"},
             "pull_request": {"number": 7, "draft": False}, "installation": {"id": 42}}  # fmt: skip


async def test_webhook_rejects_bad_signatures(db):
    c, svc = client(db)
    async with c:
        assert (await c.post("/webhook", content=b"{}")).status_code == 401
        r = await c.post("/webhook", **hook(PR_OPENED, secret="wrong"))
        assert r.status_code == 401 and not svc.queue.events


async def test_webhook_queues_once_per_delivery(db):
    c, svc = client(db)
    async with c:
        r = await c.post("/webhook", **hook(PR_OPENED))
        assert r.status_code == 202 and r.json() == {"status": "queued"}
        r = await c.post("/webhook", **hook(PR_OPENED))  # GitHub redelivers the same event
        assert r.json() == {"status": "duplicate"}
    assert svc.queue.events == [
        {"repo": "me/sandbox", "number": 7, "delivery_id": "d-1", "installation_id": 42}
    ]


async def test_webhook_ignores_other_events_and_drafts(db):
    c, svc = client(db)
    async with c:
        r = await c.post("/webhook", **hook({"action": "created"}, "d-2", event="issue_comment"))
        assert r.json() == {"status": "ignored"}
        draft = {**PR_OPENED, "pull_request": {"number": 8, "draft": True}}
        assert (await c.post("/webhook", **hook(draft, "d-3"))).json() == {"status": "ignored"}
    assert svc.queue.events == []


async def test_admin_api_needs_the_token(db):
    c, _ = client(db)
    async with c:
        assert (await c.get("/proposals")).status_code == 401
        assert (await c.get("/proposals", headers={"Authorization": "Bearer x"})).status_code == 401
        assert (await c.get("/proposals", headers=AUTH)).json() == []
    c, _ = client(db, token="")
    async with c:
        assert (await c.get("/proposals", headers=AUTH)).status_code == 503


async def make_proposal(db) -> Proposal:
    triage = TriageOutput.model_validate_json(json.dumps({
        "summary": "Fixes a crash.", "type": "bug", "risk": "low", "labels": ["kind/bug"],
        "reviewers": ["alice"], "related_issues": [], "rationale": "Small fix."}))  # fmt: skip
    async with db() as s:
        run = PRRun(repo="me/sandbox", pr_number=7, head_sha="f" * 40, state="proposed")
        s.add(run)
        await s.flush()
        content = build_content("me/sandbox", 7, "f" * 40, triage, "Comment.",
                                {"changed_files": ["pkg/api/handler.go"]})  # fmt: skip
        p = Proposal(run_id=run.id, repo="me/sandbox", pr_number=7, head_sha="f" * 40,
                     content=content, content_hash=content_hash(content))  # fmt: skip
        s.add(p)
        await s.commit()
        return p


async def test_approve_then_execute_writes_once_and_updates_memory(db):
    p = await make_proposal(db)
    c, svc = client(db)
    base = f"/proposals/{p.id}/actions"
    async with c:
        shown = (await c.get(f"/proposals/{p.id}", headers=AUTH)).json()
        assert [a["id"] for a in shown["actions"]] == ["labels", "reviewers", "comment"]
        for action in ("labels", "reviewers"):
            r = await c.post(
                f"{base}/{action}/decision",
                headers=AUTH,
                json={"decision": "approve", "content_hash": shown["content_hash"]},
            )
            assert r.status_code == 200
            assert (await c.post(f"{base}/{action}/execute", headers=AUTH)).status_code == 200
        replay = await c.post(f"{base}/labels/execute", headers=AUTH)
        assert replay.status_code == 409 and replay.json()["detail"]["refused"] == "replayed"
        unapproved = await c.post(f"{base}/comment/execute", headers=AUTH)
        assert unapproved.json()["detail"]["refused"] == "unapproved"
        after = (await c.get(f"/proposals/{p.id}", headers=AUTH)).json()
        assert [(a["decision"], a["execution"]) for a in after["actions"]] == [
            ("approve", "done"), ("approve", "done"), (None, None)]  # fmt: skip
    assert svc.writer.calls == [("labels", ["kind/bug"]), ("reviewers", ["alice"])]
    async with db() as s:
        assert (await s.scalar(select(LabelStat.count).where(LabelStat.label == "kind/bug"))) == 1
        hist = {(h.path_prefix, h.login) for h in await s.scalars(select(ReviewerHistory))}
        assert ("pkg/api/handler.go", "alice") in hist and ("pkg/", "alice") in hist


async def test_decision_on_stale_content_is_refused(db):
    p = await make_proposal(db)
    c, _ = client(db)
    async with c:
        r = await c.post(
            f"/proposals/{p.id}/actions/labels/decision",
            headers=AUTH,
            json={"decision": "approve", "content_hash": "sha256:" + "0" * 64},
        )
        assert r.status_code == 409 and r.json()["detail"]["refused"] == "stale_or_tampered"
        assert (await c.get("/proposals/not-a-uuid", headers=AUTH)).status_code == 404


async def test_webhook_to_proposal_through_the_queue(db):
    """Signed webhook → in-process queue → pipeline (fake GitHub, scripted model) → proposal."""
    from prpilot_core.pipeline import Pipeline
    from prpilot_core.queue import InProcessQueue
    from tests.test_pipeline import FakeGitHub, FakeGuard, answer, scripted

    model, calls = scripted(answer())

    async def handle(event):
        p = Pipeline(sessions=db, github=FakeGitHub(), guard=FakeGuard(), model=model,
                     count_tokens=lambda t: len(t.split()))  # fmt: skip
        await p.run(event["repo"], event["number"])

    queue = InProcessQueue(handle)
    queue.start()
    c, _ = client(db, queue=queue)
    try:
        async with c:
            assert (await c.post("/webhook", **hook(PR_OPENED, "d-9"))).status_code == 202
            await queue.drain()
            [shown] = (await c.get("/proposals", headers=AUTH)).json()
    finally:
        await queue.stop()
    assert shown["repo"] == "me/sandbox" and shown["pr_number"] == 7 and len(calls) == 1


class DownQueue:
    def __init__(self):
        self.up, self.events = False, []

    async def publish(self, event):
        if not self.up:
            raise RuntimeError("pubsub unavailable")
        self.events.append(event)


async def test_failed_publish_answers_503_and_the_redelivery_is_queued(db):
    queue = DownQueue()
    c, _ = client(db, queue=queue)
    async with c:
        assert (await c.post("/webhook", **hook(PR_OPENED, "d-5"))).status_code == 503
        queue.up = True  # GitHub redelivers with the same delivery id
        r = await c.post("/webhook", **hook(PR_OPENED, "d-5"))
    assert r.json() == {"status": "queued"} and len(queue.events) == 1


async def test_each_service_serves_only_its_part(db):
    def app_client(parts):
        svc = client(db)[1]
        app = create_app(svc, parts=parts)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    execute = "/proposals/00000000-0000-0000-0000-000000000000/actions/labels/execute"
    async with app_client(("webhook", "review")) as api:
        assert (await api.post(execute, headers=AUTH)).status_code == 404
        assert (await api.get("/runs", headers=AUTH)).status_code == 200
    async with app_client(("execute",)) as executor:
        assert (await executor.post("/webhook", **hook(PR_OPENED))).status_code == 404
        assert (await executor.get("/runs", headers=AUTH)).status_code == 404
        r = await executor.post(execute, headers=AUTH)
        assert r.status_code == 409 and r.json()["detail"]["refused"] == "unknown_proposal"


def test_settings_default_to_dry_run(monkeypatch):
    from prpilot_core.settings import Settings

    for k in ("EXECUTOR_DRY_RUN", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SANDBOX_REPOS", "me/sandbox, me/other ")
    s = Settings.from_env()
    assert s.dry_run is True and s.sandbox_repos == frozenset({"me/sandbox", "me/other"})
