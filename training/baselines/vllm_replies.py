"""Phase 2 baselines on Explorer: one model's raw replies to a prompt pack, with vLLM.

Reads the prompt pack written on the laptop (`python -m prpilot_data prompts`), so every
model sees byte-identical inputs and this script needs no app code. Generates once per PR,
greedy, no constrained decoding, no retries (model metric, hard rule 10). Writes replies in
the eval harness format plus a `.meta.json` naming the model, the settings and the pack's
SHA-256; scoring happens on the laptop.

Usage (inside the sbatch job):
    python training/baselines/vllm_replies.py --name qwen3-1.7b --pack <pack.jsonl> \
        --out <replies.jsonl> --model <hf repo> --revision <sha> --thinking off \
        --max-tokens 1024 --max-model-len 16384
"""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def code_commit() -> str | None:
    """The cluster copy has no .git; the laptop writes the commit it was copied from."""
    path = REPO_ROOT / "COMMIT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def main() -> None:
    from vllm import LLM, SamplingParams

    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--thinking", choices=["on", "off"], required=True)
    p.add_argument("--max-tokens", type=int, required=True)
    p.add_argument("--max-model-len", type=int, required=True)
    p.add_argument("--text-only", action="store_true", help="multimodal model: no image/video")
    p.add_argument("--limit", type=int, help="trial: only the first N PRs")
    args = p.parse_args()

    raw = args.pack.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    start = time.perf_counter()
    extra = {"limit_mm_per_prompt": {"image": 0, "video": 0}} if args.text_only else {}
    llm = LLM(
        model=args.model,
        revision=args.revision,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=0.92,
        seed=0,
        **extra,
    )
    load_s = time.perf_counter() - start

    params = SamplingParams(temperature=0.0, max_tokens=args.max_tokens, seed=0)
    start = time.perf_counter()
    outs = llm.chat(
        [r["messages"] for r in rows],
        params,
        chat_template_kwargs={"enable_thinking": args.thinking == "on"},
    )
    gen_s = time.perf_counter() - start

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        for row, o in zip(rows, outs, strict=True):
            line = {
                "repo": row["repo"],
                "number": row["number"],
                "text": o.outputs[0].text,
                "finish_reason": o.outputs[0].finish_reason,
                "prompt_tokens": len(o.prompt_token_ids),
                "output_tokens": len(o.outputs[0].token_ids),
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    import torch

    meta = {
        "name": args.name,
        "model": {"repo": args.model, "revision": args.revision},
        "pack_file": args.pack.name,
        "pack_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "prs": len(rows),
        "trial_limit": args.limit,
        "settings": {
            "thinking": args.thinking,
            "max_tokens": args.max_tokens,
            "max_model_len": args.max_model_len,
            "temperature": 0.0,
            "seed": 0,
            "constrained_decoding": False,
            "retries": 0,
        },
        "timing_s": {
            "load": round(load_s, 1),
            "generate": round(gen_s, 1),
            "per_pr": round(gen_s / len(rows), 3),
        },
        "gpu": torch.cuda.get_device_name(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "code_commit": code_commit(),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("vllm", "torch", "transformers")},
    }
    meta_path = args.out.with_name(args.out.name.removesuffix(".jsonl") + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"load {load_s:.0f}s, generate {gen_s:.0f}s for {len(rows)} PRs; wrote {args.out}")


if __name__ == "__main__":
    main()
