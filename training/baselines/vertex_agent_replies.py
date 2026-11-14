"""Phase 2 system pass for Gemini: the triage agent (Pydantic AI, validator retries) on Vertex AI
over the sampled prompt pack (laptop).

The Vertex counterpart of agent_replies.py: same agent, same retry limit, same reply format,
so the eval harness scores what the system finally returned. Same model and settings as the raw
Gemini baseline (vertex_replies.py). Paid, so the same hard cap applies: before each PR it
reserves the worst case for every allowed request (each retry resends the conversation), and
it stops starting PRs when spent + reserved would pass --max-usd. Resumable. Needs the token
counts from `vertex_replies.py estimate`.

Usage:
    uv run --group vertex python training/baselines/vertex_agent_replies.py \
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

from agent_replies import deps_for, response_texts
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider
from vertex_replies import (
    CONCURRENCY,
    LOCATION,
    MAX_OUTPUT_TOKENS,
    MODEL,
    REPLIES_DIR,
    SEED,
    THINKING_LEVEL,
    client_from_env,
    cost,
    counts_path,
    key,
    read_pack,
)

from prpilot_core.agents.triage import DEFAULT_OUTPUT_RETRIES, SYSTEM_PROMPT, build_triage_agent
from prpilot_data.records import require_clean_tree, run_metadata

REQUESTS = DEFAULT_OUTPUT_RETRIES + 1
# A retry resends the conversation: the prompt, earlier replies and the validator's message
# (which can list every label in the repo). Generous upper bound on that message.
RETRY_MESSAGE_TOKENS = 4000


def worst_case(input_tokens: int, price_in: float, price_out: float) -> float:
    total_in = sum(
        input_tokens + i * (MAX_OUTPUT_TOKENS + RETRY_MESSAGE_TOKENS) for i in range(REQUESTS)
    )
    return cost(total_in, REQUESTS * MAX_OUTPUT_TOKENS, price_in, price_out)


async def run_one(agent, row: dict, settings) -> dict:
    start = time.perf_counter()
    error = api_error = ""
    with capture_run_messages() as messages:
        try:
            await agent.run(
                row["messages"][1]["content"], deps=deps_for(row), model_settings=settings
            )
        except ModelHTTPError as e:  # rate limit or server error: not a model answer
            api_error = f"{e.status_code}: {e}"[:300]
        except Exception as e:  # retries exhausted
            error = f"{type(e).__name__}: {e}"[:500]
    latency_s = time.perf_counter() - start
    texts = response_texts(messages)
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    # Gemini's output_tokens already include thinking tokens.
    tokens_in = sum(m.usage.input_tokens for m in responses)
    tokens_out = sum(m.usage.output_tokens for m in responses)
    return {
        "repo": row["repo"],
        "number": row["number"],
        "text": texts[-1] if texts else "",
        "agent_valid": not error,
        "agent_error": error,
        "requests": len(texts),
        "first_text": texts[0] if texts else "",
        "prompt_tokens": tokens_in,
        "output_tokens": tokens_out,
        "latency_s": round(latency_s, 2),
        **({"api_error": api_error} if api_error else {}),
    }


async def run_all(agent, todo, counts, args, spent: float, settings, out_f) -> tuple[float, int]:
    sem = asyncio.Semaphore(CONCURRENCY)
    budget = asyncio.Condition()
    state = {"spent": spent, "reserved": 0.0, "skipped": 0}

    async def one(row: dict) -> None:
        worst = worst_case(counts[key(row)], args.price_in, args.price_out)
        async with sem:
            async with budget:
                # Same rule as vertex_replies.py: wait for in-flight PRs, skip only when idle.
                while state["spent"] + state["reserved"] + worst > args.max_usd:
                    if state["reserved"] == 0:
                        state["skipped"] += 1
                        return
                    await budget.wait()
                state["reserved"] += worst
            usd, res = 0.0, None
            try:
                res = await run_one(agent, row, settings)
                usd = cost(
                    res["prompt_tokens"], res["output_tokens"], args.price_in, args.price_out
                )
            finally:
                async with budget:
                    state["reserved"] -= worst
                    state["spent"] += usd
                    budget.notify_all()
        if res is None:
            return
        out_f.write(json.dumps({**res, "cost_usd": round(usd, 6)}, ensure_ascii=False) + "\n")
        out_f.flush()

    await asyncio.gather(*(one(row) for row in todo))
    return state["spent"], state["skipped"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--price-in", type=float, required=True, help="USD per 1M input tokens")
    p.add_argument("--price-out", type=float, required=True, help="USD per 1M output tokens")
    p.add_argument("--max-usd", type=float, required=True, help="hard spending cap")
    args = p.parse_args()
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    require_clean_tree()
    client = client_from_env()
    raw, rows = read_pack(args.pack)
    pack_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    cpath = counts_path(args.pack)
    if not cpath.exists():
        raise SystemExit(f"no token counts at {cpath}; run `vertex_replies.py estimate` first")
    saved = json.loads(cpath.read_text(encoding="utf-8"))
    if saved["pack_sha256"] != pack_sha or saved["model"] != MODEL:
        raise SystemExit("token counts were made for another pack or model; re-run `estimate`")
    counts = saved["input_tokens"]
    stale = sum(r["messages"][0]["content"] != SYSTEM_PROMPT for r in rows)
    if stale:
        raise SystemExit(f"{stale} pack rows have a different system prompt; re-render the pack")

    agent = build_triage_agent(
        GoogleModel(MODEL, provider=GoogleProvider(client=client)),
        output_retries=DEFAULT_OUTPUT_RETRIES,
    )
    settings = GoogleModelSettings(
        max_tokens=MAX_OUTPUT_TOKENS,
        seed=SEED,
        google_thinking_config={"thinking_level": THINKING_LEVEL},
    )

    name = f"{MODEL}-system_{args.pack.stem.removeprefix('prompts_')}"
    out = REPLIES_DIR / f"{name}.jsonl"
    old = (
        [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        if out.exists()
        else []
    )
    done = {key(r): r for r in old}
    spent = sum(r["cost_usd"] for r in old)  # every attempt, including re-run API errors
    # PRs that hit an API error (rate limit, server) are run again; their spend still counts.
    todo = [r for r in rows if key(r) not in done or "api_error" in done[key(r)]]
    print(f"{len(rows)} PRs, {len(todo)} to run, ${spent:.2f} already spent, cap ${args.max_usd}")

    start = time.perf_counter()
    REPLIES_DIR.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8", newline="") as f:
        spent, skipped = asyncio.run(run_all(agent, todo, counts, args, spent, settings, f))
    run_s = time.perf_counter() - start

    every = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    final = {key(r): r for r in every}  # the last attempt per PR wins
    lines = [r for r in final.values() if "api_error" not in r]
    requests = Counter(r["requests"] for r in lines)
    errors = Counter(r["agent_error"].split(":", 1)[0] for r in lines if r["agent_error"])
    meta = {
        "name": name,
        "model": {"repo": f"vertex-ai:{MODEL}", "revision": MODEL},
        "pack_file": args.pack.name,
        "pack_sha256": pack_sha,
        "prs": len(rows),
        "not_run_cap": len(rows) - len(final),
        "api_errors": len(final) - len(lines),
        "settings": {
            "mode": "system (Pydantic AI agent, validator retries)",
            "location": LOCATION,
            "thinking_level": THINKING_LEVEL.value,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": "model default",
            "seed": SEED,
            "constrained_decoding": False,
            "retries": DEFAULT_OUTPUT_RETRIES,
            "concurrency": CONCURRENCY,
        },
        "agent": {
            "valid_after_retries": sum(r["agent_valid"] for r in lines),
            "requests_per_pr": {str(k): v for k, v in sorted(requests.items())},
            "errors": dict(errors),
        },
        "cost": {
            "usd_estimated": round(sum(r["cost_usd"] for r in every), 4),
            "price_per_mtok": {"input": args.price_in, "output_incl_thinking": args.price_out},
            "cap_usd": args.max_usd,
            "note": "computed from usage metadata at the prices passed; the bill is the truth",
        },
        "timing_s": {"this_session": round(run_s, 1)},
        "meta": run_metadata(["google-genai", "pydantic-ai-slim"]),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("google-genai", "pydantic-ai-slim", "pydantic")},
    }
    out.with_name(f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    a = meta["agent"]
    print(
        f"{a['valid_after_retries']}/{len(rows)} valid after retries; requests "
        f"{a['requests_per_pr']}; skipped by cap {skipped}; ${spent:.2f} spent; wrote {out}"
    )


if __name__ == "__main__":
    main()
