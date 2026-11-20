"""The worker's HTTP face on Cloud Run: Pub/Sub pushes each queued event to `POST /pubsub`.

The answer is Pub/Sub's ack: 204 acks the message; any other status makes Pub/Sub deliver it
again with backoff, and after the subscription's maximum attempts it goes to the dead-letter
topic. So the endpoint answers 500 when a run failed in a way worth retrying (see
`prpilot_core.pipeline.should_retry`), and 400 for a body that is not a push message. Only
Pub/Sub's own service account may call it (Cloud Run IAM checks the push's OIDC token).
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response

from prpilot_core.queue import decode_push


def create_worker_app(handle: Callable[[dict], Awaitable[bool]]) -> FastAPI:
    app = FastAPI(title="PRPilot worker")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/pubsub", status_code=204)
    async def push(request: Request) -> Response:
        try:
            event = decode_push(await request.json())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if await handle(event):
            raise HTTPException(500, "run failed; deliver the event again")
        return Response(status_code=204)

    return app
