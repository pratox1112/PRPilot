"""The Pub/Sub queue (publish over REST with a metadata-server token) and the worker's push
endpoint (ack, retry, poison), on mocked HTTP: no Google Cloud needed."""

import base64
import json

import httpx
import pytest

from prpilot_core.queue import METADATA_TOKEN_URL, PubSubQueue, decode_push
from services.worker.push import create_worker_app

pytestmark = pytest.mark.anyio
EVENT = {"repo": "me/sandbox", "number": 7, "delivery_id": "d-1", "installation_id": 42}


def push_body(event) -> dict:
    data = base64.b64encode(json.dumps(event).encode()).decode()
    return {"message": {"data": data, "messageId": "1"}, "subscription": "projects/p/subs/s"}


class Recorder:
    def __init__(self, publish_status=200):
        self.requests, self.publish_status = [], publish_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == METADATA_TOKEN_URL:
            assert request.headers["Metadata-Flavor"] == "Google"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        return httpx.Response(self.publish_status, json={"messageIds": ["1"]})


async def test_publish_sends_the_event_with_a_cached_token():
    rec, now = Recorder(), [0.0]
    q = PubSubQueue("proj", "pr-events", httpx.AsyncClient(transport=httpx.MockTransport(rec)),
                    clock=lambda: now[0])  # fmt: skip
    await q.publish(EVENT)
    now[0] = 3000.0  # still valid (expires at 3599, refreshed a minute early)
    await q.publish(EVENT)
    now[0] = 3550.0
    await q.publish(EVENT)
    urls = [str(r.url) for r in rec.requests]
    assert urls.count(METADATA_TOKEN_URL) == 2 and len(urls) == 5
    pub = rec.requests[1]
    assert str(pub.url) == "https://pubsub.googleapis.com/v1/projects/proj/topics/pr-events:publish"
    assert pub.headers["Authorization"] == "Bearer tok"
    [msg] = json.loads(pub.content)["messages"]
    assert decode_push({"message": msg}) == EVENT
    assert msg["attributes"] == {"delivery_id": "d-1"}


async def test_failed_publish_raises():
    q = PubSubQueue("p", "t", httpx.AsyncClient(transport=httpx.MockTransport(Recorder(503))))
    with pytest.raises(httpx.HTTPStatusError):
        await q.publish(EVENT)


def worker_client(handle):
    app = create_worker_app(handle)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w")


async def test_push_acks_after_the_run_and_asks_again_when_it_should():
    seen, retry = [], [False]

    async def handle(event):
        seen.append(event)
        return retry[0]

    async with worker_client(handle) as c:
        assert (await c.post("/pubsub", json=push_body(EVENT))).status_code == 204
        retry[0] = True
        assert (await c.post("/pubsub", json=push_body(EVENT))).status_code == 500
    assert seen == [EVENT, EVENT]


async def test_push_rejects_a_body_that_is_not_a_push_message():
    async def handle(event):
        raise AssertionError("must not be called")

    async with worker_client(handle) as c:
        assert (await c.post("/pubsub", json={"nope": 1})).status_code == 400
        bad = {"message": {"data": "!!not base64!!"}}
        assert (await c.post("/pubsub", json=bad)).status_code == 400
