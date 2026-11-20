"""The event queue between the webhook receiver and the worker (roadmap §9.2, "fast ack").

The webhook only verifies, dedupes and publishes; the worker consumes. `InProcessQueue` is the
local implementation (an asyncio queue with one consumer task); `PubSubQueue` publishes to a
Pub/Sub topic, which pushes each message to the worker service (services/worker/main.py). A
handler that raises is logged and the event dropped here; with Pub/Sub, redelivery with
backoff and the dead-letter topic take over.
"""

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

log = logging.getLogger("prpilot.queue")

PUBSUB_API = "https://pubsub.googleapis.com/v1"
# On Cloud Run, the service's own service account; no key file anywhere.
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


class EventQueue(Protocol):
    async def publish(self, event: dict) -> None: ...


class InProcessQueue:
    def __init__(self, handler: Callable[[dict], Awaitable[None]]):
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._handler = handler
        self._task: asyncio.Task | None = None

    async def publish(self, event: dict) -> None:
        await self._queue.put(event)

    def start(self) -> None:
        self._task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._handler(event)
            except Exception:
                log.exception("event failed: %s", event)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """Wait until every published event has been handled (tests, shutdown)."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()


class PubSubQueue:
    """Publishes each event as one Pub/Sub message (JSON in `data`, the delivery id as an
    attribute) through the REST API, with an access token from the metadata server that is
    cached until a minute before it expires. A failed publish raises, so the webhook can
    answer with an error and GitHub's delivery is not lost."""

    def __init__(self, project: str, topic: str, http: httpx.AsyncClient, clock=time.monotonic):
        self.url = f"{PUBSUB_API}/projects/{project}/topics/{topic}:publish"
        self.http, self.clock = http, clock
        self._token, self._expires = "", 0.0

    async def _access_token(self) -> str:
        if self._token and self.clock() < self._expires - 60:
            return self._token
        r = await self.http.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        r.raise_for_status()
        data = r.json()
        self._token, self._expires = data["access_token"], self.clock() + data["expires_in"]
        return self._token

    async def publish(self, event: dict) -> None:
        data = base64.b64encode(json.dumps(event).encode()).decode()
        message = {"data": data, "attributes": {"delivery_id": str(event.get("delivery_id", ""))}}
        r = await self.http.post(
            self.url,
            json={"messages": [message]},
            headers={"Authorization": f"Bearer {await self._access_token()}"},
        )
        r.raise_for_status()


def decode_push(envelope: dict) -> dict:
    """The event inside a Pub/Sub push request body; ValueError if it is not one."""
    try:
        return json.loads(base64.b64decode(envelope["message"]["data"]))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"not a Pub/Sub push message: {e}") from e
