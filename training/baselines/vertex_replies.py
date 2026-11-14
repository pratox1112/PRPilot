"""Phase 2 frontier baseline: Gemini Flash on Vertex AI over a sampled prompt pack (laptop).

Paid API, so it runs in two steps and never spends more than the cap:
  estimate  counts every prompt's input tokens with Vertex `countTokens` (free) and prints the
            cost range at the prices you pass, read from the official Vertex AI pricing page.
            Saves the counts; `run` refuses to start without them.
  run       one request per PR, a few at a time. Before each request it reserves the worst
            case (its input tokens + max_output_tokens, thinking billed as output); it stops
            sending when spent + reserved would pass --max-usd. Resumable: PRs already
            answered are skipped and their cost counts toward the cap.

Vertex AI only (billed to GCP_PROJECT from .env, so the free-trial credit applies). Refuses to
run when an AI Studio key (GOOGLE_API_KEY / GEMINI_API_KEY) is set. Auth is Application
Default Credentials (`gcloud auth application-default login`).

Usage:
    uv run --group vertex python training/baselines/vertex_replies.py estimate \
        --pack data/processed/prompts_val_s500.jsonl --price-in P --price-out Q
    uv run --group vertex python training/baselines/vertex_replies.py run \
        --pack data/processed/prompts_val_s500.jsonl --price-in P --price-out Q --max-usd 10
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from prpilot_data.records import REPO_ROOT, require_clean_tree, run_metadata

MODEL = "gemini-3.8-flash"
LOCATION = "global"
MAX_OUTPUT_TOKENS = 8192
THINKING_LEVEL = types.ThinkingLevel.LOW  # lowest level this model accepts (no MINIMAL)
SEED = 0
CONCURRENCY = 8
RETRY_WAITS_S = (10, 30, 90)  # rate limits and server errors only
REPLIES_DIR = REPO_ROOT / "data" / "processed" / "replies"
COUNTS_DIR = REPO_ROOT / "data" / "interim"


def client_from_env() -> genai.Client:
    load_dotenv(REPO_ROOT / ".env")
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(key):
            raise SystemExit(f"{key} is set: that is AI Studio, which the credit does not cover")
    project = os.environ.get("GCP_PROJECT")
    if not project:
        raise SystemExit("GCP_PROJECT is missing from .env")
    return genai.Client(vertexai=True, project=project, location=LOCATION)


def read_pack(path: Path) -> tuple[bytes, list[dict]]:
    raw = path.read_bytes()
    return raw, [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]


def key(row: dict) -> str:
    return f"{row['repo']}#{row['number']}"


def counts_path(pack: Path) -> Path:
    return COUNTS_DIR / f"vertex_counts_{pack.stem}.json"


def config(system: str) -> types.GenerateContentConfig:
    # Temperature left at the model default: Google advises against lowering it for Gemini 3+.
    return types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        seed=SEED,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
    )


def cost(tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
    return (tokens_in * price_in + tokens_out * price_out) / 1e6


def cmd_estimate(args) -> None:
    client = client_from_env()
    raw, rows = read_pack(args.pack)
    counts = {}
    for i, row in enumerate(rows, 1):
        system, user = row["messages"][0]["content"], row["messages"][1]["content"]
        r = client.models.count_tokens(
            model=MODEL,
            contents=user,
            config=types.CountTokensConfig(system_instruction=system),
        )
        counts[key(row)] = r.total_tokens
        if i % 100 == 0:
            print(f"counted {i}/{len(rows)}")
    total_in = sum(counts.values())
    path = counts_path(args.pack)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": MODEL,
                "pack_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "input_tokens": counts,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    n = len(rows)
    print(f"{n} PRs, {total_in:,} input tokens (mean {total_in / n:,.0f}); saved {path}")
    for label, out in (("typical (2,000 out/PR)", 2000), ("worst case", MAX_OUTPUT_TOKENS)):
        usd = cost(total_in, out * n, args.price_in, args.price_out)
        print(f"  {label}: ${usd:.2f}")


async def ask(client, row: dict) -> dict:
    system, user = row["messages"][0]["content"], row["messages"][1]["content"]
    start = time.perf_counter()
    for wait in (*RETRY_WAITS_S, None):
        try:
            resp = await client.aio.models.generate_content(
                model=MODEL, contents=user, config=config(system)
            )
            break
        except errors.APIError as e:
            retryable = e.code == 429 or (e.code or 0) >= 500
            if wait is None or not retryable:
                # Not a model answer: left out of the cost, re-asked on the next `run`.
                return {"text": "", "api_error": f"{e.code}: {e.message}"[:300]}
            await asyncio.sleep(wait)
    latency = time.perf_counter() - start
    u = resp.usage_metadata
    cand = resp.candidates[0] if resp.candidates else None
    return {
        "text": resp.text or "",
        "finish_reason": cand.finish_reason.name.lower() if cand and cand.finish_reason else None,
        "prompt_tokens": u.prompt_token_count,
        "output_tokens": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
        "thought_tokens": u.thoughts_token_count or 0,
        "latency_s": round(latency, 2),
        "model_version": resp.model_version,
    }


async def run_all(client, todo, counts, args, spent: float, out_f) -> tuple[float, int]:
    sem = asyncio.Semaphore(CONCURRENCY)
    budget = asyncio.Condition()
    state = {"spent": spent, "reserved": 0.0, "skipped": 0}

    async def one(row: dict) -> None:
        worst = cost(counts[key(row)], MAX_OUTPUT_TOKENS, args.price_in, args.price_out)
        async with sem:
            async with budget:
                # Over the cap with requests in flight: wait for them to settle (their real
                # cost is usually far below the reserved worst case). Nothing in flight: skip.
                while state["spent"] + state["reserved"] + worst > args.max_usd:
                    if state["reserved"] == 0:
                        state["skipped"] += 1
                        return
                    await budget.wait()
                state["reserved"] += worst
            res = {"text": "", "api_error": "not sent"}
            try:
                res = await ask(client, row)
            finally:
                usd = 0.0
                if "api_error" not in res:
                    usd = cost(
                        res["prompt_tokens"], res["output_tokens"], args.price_in, args.price_out
                    )
                async with budget:
                    state["reserved"] -= worst
                    state["spent"] += usd
                    budget.notify_all()
        line = {"repo": row["repo"], "number": row["number"], **res, "cost_usd": round(usd, 6)}
        out_f.write(json.dumps(line, ensure_ascii=False) + "\n")
        out_f.flush()

    await asyncio.gather(*(one(row) for row in todo))
    return state["spent"], state["skipped"]


def cmd_run(args) -> None:
    require_clean_tree()
    client = client_from_env()
    raw, rows = read_pack(args.pack)
    pack_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    cpath = counts_path(args.pack)
    if not cpath.exists():
        raise SystemExit(f"no token counts at {cpath}; run `estimate` first")
    saved = json.loads(cpath.read_text(encoding="utf-8"))
    if saved["pack_sha256"] != pack_sha or saved["model"] != MODEL:
        raise SystemExit("token counts were made for another pack or model; re-run `estimate`")
    counts = saved["input_tokens"]

    name = f"{MODEL}_{args.pack.stem.removeprefix('prompts_')}"
    out = REPLIES_DIR / f"{name}.jsonl"
    done: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done[key(r)] = r
    spent = sum(r.get("cost_usd", 0.0) for r in done.values())
    todo = [r for r in rows if key(r) not in done or "api_error" in done[key(r)]]
    print(f"{len(rows)} PRs, {len(todo)} to ask, ${spent:.2f} already spent, cap ${args.max_usd}")

    start = time.perf_counter()
    REPLIES_DIR.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8", newline="") as f:
        spent, skipped = asyncio.run(run_all(client, todo, counts, args, spent, f))
    run_s = time.perf_counter() - start

    final: dict[str, dict] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        final[key(r)] = r  # the last answer per PR wins (retried API errors)
    answered = [r for r in final.values() if "api_error" not in r]
    meta = {
        "name": name,
        "model": {
            "repo": f"vertex-ai:{MODEL}",
            "revision": sorted({r.get("model_version") or "" for r in answered}),
        },
        "pack_file": args.pack.name,
        "pack_sha256": pack_sha,
        "prs": len(rows),
        "answered": len(answered),
        "api_errors": len(final) - len(answered),
        "not_asked_cap": len(rows) - len(final),
        "settings": {
            "location": LOCATION,
            "thinking_level": THINKING_LEVEL.value,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": "model default",
            "seed": SEED,
            "constrained_decoding": False,
            "retries": 0,
            "concurrency": CONCURRENCY,
        },
        "cost": {
            "usd_estimated": round(sum(r["cost_usd"] for r in answered), 4),
            "price_per_mtok": {"input": args.price_in, "output_incl_thinking": args.price_out},
            "cap_usd": args.max_usd,
            "note": "computed from usage metadata at the prices passed; the bill is the truth",
        },
        "finish_reasons": dict(Counter(r.get("finish_reason") for r in answered)),
        "timing_s": {"this_session": round(run_s, 1)},
        "meta": run_metadata(["google-genai"]),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {"google-genai": version("google-genai")},
    }
    meta_path = out.with_name(f"{name}.meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(
        f"answered {len(answered)}/{len(rows)}, API errors {meta['api_errors']}, "
        f"skipped by cap {skipped}; ${spent:.2f} spent; wrote {out}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "run"):
        s = sub.add_parser(name)
        s.add_argument("--pack", type=Path, required=True)
        s.add_argument("--price-in", type=float, required=True, help="USD per 1M input tokens")
        s.add_argument("--price-out", type=float, required=True, help="USD per 1M output tokens")
        if name == "run":
            s.add_argument("--max-usd", type=float, required=True, help="hard spending cap")
    args = p.parse_args()
    if args.cmd == "estimate":
        cmd_estimate(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
