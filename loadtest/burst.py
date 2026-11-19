"""Burst test (roadmap §9.3): every PR of a replay bundle sent to the deployed api at once.

    uv run python -m loadtest.burst --label burst --api API_URL
        --bundle data/processed/replay/burst_val500.jsonl

All webhooks go out as fast as `--concurrency` parallel requests allow, then a seeded
`--redeliver` share of them is sent again with the same delivery id (as GitHub redelivers),
which the api must drop. Then the runs are polled every `--poll` seconds until every
accepted PR has a final run, or `--timeout` seconds pass. Events the worker cannot take at
once wait in Pub/Sub, which retries them with backoff (load runs raise the attempts, see
`max_delivery_attempts`). One line per PR goes to data/processed/replay/<label>.sends.jsonl,
in the format `replay.measure report` reads, plus the redelivery and its answer; the file is
rewritten after every poll, so an interrupted run keeps what it saw. Starts only on a clean,
committed tree (hard rule 1).
"""

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import httpx

from prpilot_data.records import read_jsonl, require_clean_tree
from replay.send import SENDS_DIR, is_final, now, post_webhook, secret_values

MAX_LIST = 20_000  # services.api.app.MAX_LIST


async def burst(records: list[dict], http: httpx.AsyncClient, api: str, secret: str,
                admin: dict, label: str, redeliver: float, seed: int, concurrency: int,
                timeout: float, poll: float, out: Path) -> list[dict]:  # fmt: skip
    prs = [r["pr"] for r in records]
    again = sorted(random.Random(seed).sample(range(len(prs)), round(len(prs) * redeliver)))
    gate = asyncio.Semaphore(concurrency)

    async def post(pr):
        async with gate:
            return await post_webhook(http, api, secret, label, pr)

    sent = list(await asyncio.gather(*(post(pr) for pr in prs)))
    print(f"sent {len(sent)}: {count(r['http_status'] for r in sent)}")
    redelivered = await asyncio.gather(*(post(prs[i]) for i in again))
    for i, r in zip(again, redelivered, strict=True):
        sent[i] = {**sent[i], "redelivered_at": r["sent_at"],
                   "redelivery_answer": r["answer"]}  # fmt: skip
    print(f"redelivered {len(redelivered)}: {count(str(r['answer']) for r in redelivered)}")

    for r in sent:
        r.update(final=None, outcome="unfinished" if r["http_status"] == 202 else "not accepted")
    pending = {(r["repo"], r["number"], r["head_sha"]): r for r in sent if r["http_status"] == 202}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(poll)
        q = await http.get(f"{api}/runs", headers=admin, params={"limit": MAX_LIST})
        q.raise_for_status()
        for run in q.json():
            key = (run["repo"], run["pr_number"], run["head_sha"])
            if key in pending and is_final(run):
                pending.pop(key).update(final=run, seen_final_at=now(), outcome=run["state"])
        write(sent, out)
        print(f"{len(sent) - len(pending)}/{len(sent)} final")
    write(sent, out)
    return sent


def write(records: list[dict], out: Path) -> None:
    out.write_text("".join(json.dumps(r, default=str) + "\n" for r in records), encoding="utf-8")


def count(values) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--redeliver", type=float, default=0.1, help="share of PRs sent twice")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=20, help="parallel webhook requests")
    ap.add_argument("--timeout", type=float, default=4 * 3600, help="seconds to wait in all")
    ap.add_argument("--poll", type=float, default=15)
    a = ap.parse_args()
    require_clean_tree()
    secret, token = secret_values()
    records = read_jsonl(a.bundle)
    SENDS_DIR.mkdir(parents=True, exist_ok=True)
    out = SENDS_DIR / f"{a.label}.sends.jsonl"

    async def go():
        async with httpx.AsyncClient(timeout=60) as http:
            return await burst(records, http, a.api.rstrip("/"), secret,
                               {"Authorization": f"Bearer {token}"}, a.label, a.redeliver,
                               a.seed, a.concurrency, a.timeout, a.poll, out)  # fmt: skip

    done = asyncio.run(go())
    print(f"{len(done)} PRs -> {out}: {count(r['outcome'] for r in done)}")


if __name__ == "__main__":
    main()
