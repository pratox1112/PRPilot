"""The event queue between the webhook receiver and the worker (roadmap §9.2, "fast ack").

The webhook only verifies, dedupes and publishes; the worker consumes. `InProcessQueue` is the
local implementation (an asyncio queue with one consumer task); Phase 6 adds a Pub/Sub one with
the same `publish` interface, so neither side changes. A handler that raises is logged and
the event dropped here; with Pub/Sub, redelivery and the dead-letter topic take over.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

log = logging.getLogger("prpilot.queue")


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
