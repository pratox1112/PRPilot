"""Send each PR of a replay bundle to the deployed api as a signed pull_request.opened webhook.

    uv run python -m replay.send --bundle data/processed/replay/pilot_val50.jsonl \
        --api https://prpilot-api-....run.app --label pilot --in-flight 3

At most `--in-flight` PRs are open at once: the next is sent when a run of an earlier one
reaches a final state (proposed, quarantined, or failed with no further delivery). Each
webhook is signed with GITHUB_WEBHOOK_SECRET and polled with ADMIN_TOKEN (from the
environment, else .env). Delivery ids are derived from the label and the PR, so sending the
same bundle again with the same label is dropped by the api as duplicates. One line per PR
goes to `data/processed/replay/<label>.sends.jsonl`: when it was sent, the api's answer, and
the final run as polled (or "unfinished" after `--timeout` seconds); `replay.measure` turns it
into the recorded result. Starts only on a clean, committed tree (hard rule 1).
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from prpilot_core.github.webhooks import DELIVERY_HEADER, EVENT_HEADER, SIGNATURE_HEADER, sign
from prpilot_data.records import REPO_ROOT, read_jsonl, require_clean_tree

SENDS_DIR = REPO_ROOT / "data" / "processed" / "replay"
NAMESPACE = uuid.UUID("5d0f3a52-8e0b-4c43-9a3e-2f6c1b7d9e40")  # fixed: same label, same ids


def delivery_id(label: str, repo: str, number: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{label}:{repo}#{number}"))


def payload(pr: dict) -> dict:
    """The fields of a GitHub pull_request.opened payload the api reads, from open-time data."""
    return {
        "action": "opened",
        "number": pr["number"],
        "pull_request": {
            "number": pr["number"], "title": pr["title"], "draft": False,
            "user": {"login": pr["author"]}, "head": {"sha": pr["head_sha"]},
            "base": {"ref": pr["base_branch"]}, "created_at": pr["created_at"],
        },
        "repository": {"full_name": pr["repo"]},
    }  # fmt: skip


def is_final(run: dict) -> bool:
    # A failed run with no triage is delivered again by Pub/Sub (pipeline.should_retry).
    return run["state"] in ("proposed", "quarantined") or (
        run["state"] == "failed" and bool(run.get("triage"))
    )


def now() -> str:
    return datetime.now(UTC).isoformat()


async def send_one(http: httpx.AsyncClient, api: str, secret: str, admin: dict, label: str,
                   pr: dict, timeout: float, poll: float) -> dict:  # fmt: skip
    body = json.dumps(payload(pr)).encode()
    did = delivery_id(label, pr["repo"], pr["number"])
    headers = {SIGNATURE_HEADER: sign(secret, body), DELIVERY_HEADER: did,
               EVENT_HEADER: "pull_request", "Content-Type": "application/json"}  # fmt: skip
    rec = {"repo": pr["repo"], "number": pr["number"], "head_sha": pr["head_sha"],
           "delivery_id": did, "sent_at": now()}  # fmt: skip
    r = await http.post(f"{api}/webhook", content=body, headers=headers)
    rec.update(http_status=r.status_code, answer=r.json() if r.status_code < 500 else r.text)
    if r.status_code != 202:
        return {**rec, "final": None, "outcome": "not accepted"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll)
        q = await http.get(f"{api}/runs", headers=admin,
                           params={"repo": pr["repo"], "pr_number": pr["number"]})  # fmt: skip
        q.raise_for_status()
        run = next((x for x in q.json() if x["head_sha"] == pr["head_sha"]), None)
        if run and is_final(run):
            return {**rec, "seen_final_at": now(), "final": run, "outcome": run["state"]}
    return {**rec, "final": None, "outcome": "unfinished"}


async def send_all(records: list[dict], api: str, secret: str, token: str, label: str,
                   in_flight: int, timeout: float, poll: float,
                   out: Path) -> list[dict]:  # fmt: skip
    gate = asyncio.Semaphore(in_flight)
    admin = {"Authorization": f"Bearer {token}"}
    done: list[dict] = []
    with out.open("w", encoding="utf-8") as log:

        async def one(http, pr):
            async with gate:
                rec = await send_one(http, api, secret, admin, label, pr, timeout, poll)
            log.write(json.dumps(rec, default=str) + "\n")
            log.flush()
            done.append(rec)
            print(f"[{len(done)}/{len(records)}] {pr['repo']}#{pr['number']}: {rec['outcome']}")

        async with httpx.AsyncClient(timeout=60) as http:
            tasks = []
            for r in records:  # in bundle (opening) order; the gate holds back the rest
                tasks.append(asyncio.create_task(one(http, r["pr"])))
                await asyncio.sleep(0)
            await asyncio.gather(*tasks)
    return done


def secret_values() -> tuple[str, str]:
    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}
    secret = (env.get("GITHUB_WEBHOOK_SECRET") or "").strip()
    token = (env.get("ADMIN_TOKEN") or "").strip()
    if not secret or not token:
        raise SystemExit("GITHUB_WEBHOOK_SECRET and ADMIN_TOKEN are needed (environment or .env)")
    return secret, token


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--in-flight", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=900, help="seconds to wait for each run")
    ap.add_argument("--poll", type=float, default=5)
    a = ap.parse_args()
    require_clean_tree()
    secret, token = secret_values()
    records = read_jsonl(a.bundle)
    SENDS_DIR.mkdir(parents=True, exist_ok=True)
    out = SENDS_DIR / f"{a.label}.sends.jsonl"
    done = asyncio.run(send_all(records, a.api.rstrip("/"), secret, token, a.label,
                                a.in_flight, a.timeout, a.poll, out))  # fmt: skip
    counts: dict[str, int] = {}
    for d in done:
        counts[d["outcome"]] = counts.get(d["outcome"], 0) + 1
    print(f"{len(done)} sent -> {out}: {counts}")


if __name__ == "__main__":
    main()
