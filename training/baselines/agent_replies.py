"""Phase 2 system pass on Explorer: the triage agent (Pydantic AI, validator retries) against a
vLLM OpenAI-compatible server, over a prompt pack.

This is the system metric (hard rule 10): the same prompts as the raw baselines, but run
through `prpilot_core.agents.triage` exactly as the app would, so validation errors are sent
back to the model up to DEFAULT_OUTPUT_RETRIES times. Each reply line holds the model's LAST
response text, so the eval harness scores what the system finally returned, plus how many
requests it took. Runs in the agent venv (uv.lock), not in the vLLM env.

Usage (inside the sbatch job, with `vllm serve` already up):
    python training/baselines/agent_replies.py --name qwen3-1.7b-system --pack <pack.jsonl> \
        --out <replies.jsonl> --base-url http://127.0.0.1:8000/v1 --served-model <name> \
        --model <hf repo> --revision <sha>
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

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from prpilot_core.agents.triage import DEFAULT_OUTPUT_RETRIES, SYSTEM_PROMPT, build_triage_agent
from prpilot_core.deps import RepoRules, TriageDeps

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_TOKENS = 1024  # same as the raw Qwen3-1.7B baseline


def code_commit() -> str | None:
    """The cluster copy has no .git; the laptop writes the commit it was copied from."""
    path = REPO_ROOT / "COMMIT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def deps_for(row: dict) -> TriageDeps:
    # Only membership is checked by the validator; the prompt itself comes from the pack.
    r = row["rules"]
    rules = RepoRules(
        labels=frozenset(r["labels"]),
        eligible_reviewers=frozenset(r["eligible_reviewers"]),
        open_issues=dict.fromkeys(r["open_issues"], ""),
    )
    return TriageDeps(rules=rules, pr_author=r["pr_author"])


def response_texts(messages) -> list[str]:
    return [
        "".join(p.content for p in m.parts if isinstance(p, TextPart))
        for m in messages
        if isinstance(m, ModelResponse)
    ]


async def run_one(agent, row: dict, settings: ModelSettings, sem: asyncio.Semaphore) -> dict:
    async with sem:
        start = time.perf_counter()
        error = ""
        with capture_run_messages() as messages:
            try:
                result = await agent.run(
                    row["messages"][1]["content"], deps=deps_for(row), model_settings=settings
                )
                usage = result.usage
            except Exception as e:  # retries exhausted, context overflow, server error
                error = f"{type(e).__name__}: {e}"[:500]
                usage = None
        latency_s = time.perf_counter() - start
    texts = response_texts(messages)
    return {
        "repo": row["repo"],
        "number": row["number"],
        "text": texts[-1] if texts else "",
        "agent_valid": not error,
        "agent_error": error,
        "requests": len(texts),
        "first_text": texts[0] if texts else "",
        "prompt_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "latency_s": round(latency_s, 2),
    }


async def run_all(agent, rows, settings, concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*(run_one(agent, r, settings, sem) for r in rows))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--served-model", required=True)
    p.add_argument("--model", required=True, help="HF repo, recorded in the meta")
    p.add_argument("--revision", required=True)
    p.add_argument("--max-model-len", type=int, required=True, help="recorded in the meta")
    p.add_argument("--concurrency", type=int, default=64)
    p.add_argument("--limit", type=int, help="trial: only the first N PRs")
    args = p.parse_args()
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    raw = args.pack.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if args.limit:
        rows = rows[: args.limit]
    # The agent sends its own system prompt; it must be the one the pack was rendered with.
    stale = sum(r["messages"][0]["content"] != SYSTEM_PROMPT for r in rows)
    if stale:
        raise SystemExit(f"{stale} pack rows have a different system prompt; re-render the pack")

    model = OpenAIChatModel(
        args.served_model,
        provider=OpenAIProvider(base_url=args.base_url, api_key="local"),
    )
    agent = build_triage_agent(model, output_retries=DEFAULT_OUTPUT_RETRIES)
    settings = ModelSettings(
        temperature=0.0,
        seed=0,
        max_tokens=MAX_TOKENS,
        timeout=600,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    start = time.perf_counter()
    out = asyncio.run(run_all(agent, rows, settings, args.concurrency))
    run_s = time.perf_counter() - start

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        for line in out:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    n = len(out)
    requests = Counter(r["requests"] for r in out)
    errors = Counter(r["agent_error"].split(":", 1)[0] for r in out if r["agent_error"])
    meta = {
        "name": args.name,
        "model": {"repo": args.model, "revision": args.revision},
        "pack_file": args.pack.name,
        "pack_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "prs": n,
        "trial_limit": args.limit,
        "settings": {
            "mode": "system (Pydantic AI agent, validator retries)",
            "thinking": "off",
            "max_tokens": MAX_TOKENS,
            "max_model_len": args.max_model_len,
            "temperature": 0.0,
            "seed": 0,
            "constrained_decoding": False,
            "retries": DEFAULT_OUTPUT_RETRIES,
            "concurrency": args.concurrency,
        },
        "agent": {
            "valid_after_retries": sum(r["agent_valid"] for r in out),
            "requests_per_pr": {str(k): v for k, v in sorted(requests.items())},
            "errors": dict(errors),
        },
        "timing_s": {"run": round(run_s, 1), "per_pr": round(run_s / n, 3)},
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "code_commit": code_commit(),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("pydantic-ai-slim", "pydantic", "openai")},
    }
    meta_path = args.out.with_name(args.out.name.removesuffix(".jsonl") + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    a = meta["agent"]
    print(
        f"{a['valid_after_retries']}/{n} valid after retries; requests {a['requests_per_pr']}; "
        f"{run_s:.0f}s; wrote {args.out}"
    )


if __name__ == "__main__":
    main()
