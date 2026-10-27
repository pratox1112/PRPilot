"""Task 0.3: prove that a signed webhook from the GitHub App reaches us.

A minimal FastAPI endpoint that verifies X-Hub-Signature-256, rejects bad or unsigned
requests, saves each delivery under data/raw/webhooks/ (gitignored), and records the first
verified pull_request event in results/feasibility_github_app.json. It never calls GitHub.

Usage:
    uv run uvicorn webhook_receiver:app --app-dir scripts/feasibility --port 8000
"""

import json
import os
from datetime import UTC, datetime

from common import REPO_ROOT, run_metadata, write_result
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from prpilot_core.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    verify_signature,
)

load_dotenv(REPO_ROOT / ".env")
DELIVERIES_DIR = REPO_ROOT / "data" / "raw" / "webhooks"
RESULT_NAME = "feasibility_github_app"

app = FastAPI(title="PRPilot webhook feasibility receiver")
seen: dict[str, int] = {}


def summarize(event: str, delivery: str, payload: dict) -> dict:
    pr = payload.get("pull_request") or {}
    return {
        "event": event,
        "action": payload.get("action"),
        "delivery_id": delivery,
        "repository": (payload.get("repository") or {}).get("full_name"),
        "installation_id": (payload.get("installation") or {}).get("id"),
        "pr_number": pr.get("number"),
        "received_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


@app.post("/webhook", status_code=202)
async def webhook(request: Request) -> dict:
    body = await request.body()
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(status_code=401, detail="bad or missing signature")

    delivery = request.headers.get(DELIVERY_HEADER, "unknown")
    event = request.headers.get(EVENT_HEADER, "unknown")
    seen[delivery] = seen.get(delivery, 0) + 1
    payload = json.loads(body)
    info = summarize(event, delivery, payload)
    print(f"verified {event}/{info['action']} delivery={delivery} repo={info['repository']}")

    DELIVERIES_DIR.mkdir(parents=True, exist_ok=True)
    (DELIVERIES_DIR / f"{delivery}.json").write_bytes(body)

    if event == "pull_request" and not (REPO_ROOT / "results" / f"{RESULT_NAME}.json").exists():
        write_result(
            RESULT_NAME,
            {
                "task": "0.3",
                "meta": run_metadata(["fastapi", "uvicorn"]),
                "signature_verified": True,
                "first_pull_request_event": info,
                "note": "Proves a signed webhook from the GitHub App reached the local endpoint.",
            },
        )
        print(f"recorded results/{RESULT_NAME}.json")
    return {"status": "accepted", "delivery": delivery, "duplicate": seen[delivery] > 1}
