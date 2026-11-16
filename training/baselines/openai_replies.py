"""Raw replies from an OpenAI-compatible server (llama.cpp `llama-server`), for the GGUF
re-test of the student (roadmap §6.3). Same settings as vllm_replies.py: one greedy request per
PR, thinking off, no constrained decoding, no retries; lines in the eval-harness format.

The server renders the prompt with the chat template stored in the GGUF. `prompt_tokens` is
recorded per PR so the rendering can be checked against the vLLM run on the same pack (equal
counts on every PR). Runs in the agent venv (uv.lock: openai).

Usage (inside training/slurm/gguf_replies.sbatch, with llama-server up):
    python training/baselines/openai_replies.py --name student-gguf --pack <pack.jsonl> \
        --out <replies.jsonl> --base-url http://127.0.0.1:8080/v1 --served-model prpilot-model \
        --gguf <file.gguf> --max-tokens 1024
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from openai import AsyncOpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]


def code_commit() -> str | None:
    path = REPO_ROOT / "COMMIT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


async def ask(client, model: str, row: dict, max_tokens: int, sem) -> dict:
    async with sem:
        start = time.perf_counter()
        r = await client.chat.completions.create(
            model=model,
            messages=row["messages"],
            temperature=0.0,
            seed=0,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        choice = r.choices[0]
        return {
            "repo": row["repo"],
            "number": row["number"],
            "text": choice.message.content or "",
            "finish_reason": choice.finish_reason,
            "prompt_tokens": r.usage.prompt_tokens,
            "output_tokens": r.usage.completion_tokens,
            "latency_s": round(time.perf_counter() - start, 3),
        }


async def run_all(args, rows: list[dict]) -> list[dict]:
    client = AsyncOpenAI(base_url=args.base_url, api_key="local", timeout=600, max_retries=0)
    sem = asyncio.Semaphore(args.concurrency)
    return await asyncio.gather(
        *(ask(client, args.served_model, r, args.max_tokens, sem) for r in rows)
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--served-model", required=True)
    p.add_argument("--gguf", type=Path, required=True, help="the served file, hashed into meta")
    p.add_argument("--max-tokens", type=int, required=True)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--limit", type=int, help="trial: only the first N PRs")
    args = p.parse_args()

    raw = args.pack.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if args.limit:
        rows = rows[: args.limit]
    start = time.perf_counter()
    out = asyncio.run(run_all(args, rows))
    gen_s = time.perf_counter() - start

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        for line in out:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    meta = {
        "name": args.name,
        "model": {"gguf": args.gguf.name, "sha256": file_sha256(args.gguf)},
        "server": {"llamacpp": os.environ.get("LLAMACPP_VERSION")},
        "pack_file": args.pack.name,
        "pack_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "prs": len(rows),
        "trial_limit": args.limit,
        "settings": {
            "thinking": "off",
            "max_tokens": args.max_tokens,
            "temperature": 0.0,
            "seed": 0,
            "constrained_decoding": False,
            "retries": 0,
            "concurrency": args.concurrency,
        },
        "timing_s": {"generate": round(gen_s, 1), "per_pr": round(gen_s / len(rows), 3)},
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "code_commit": code_commit(),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("openai",)},
    }
    meta_path = args.out.with_name(args.out.name.removesuffix(".jsonl") + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"generate {gen_s:.0f}s for {len(rows)} PRs; wrote {args.out}")


if __name__ == "__main__":
    main()
