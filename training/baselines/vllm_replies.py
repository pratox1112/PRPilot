"""Phase 2 baselines on Explorer: one model's raw replies to a prompt pack, with vLLM.
Also labels the train split with the teacher for Phase 3 (`--shard I/N` splits a pack over jobs)
and scores the student's LoRA checkpoints (`--lora-dir`: every checkpoint in a training run's
output folder, one replies file each, with the base model loaded once).

Reads the prompt pack written on the laptop (`python -m prpilot_data prompts`), so every
model sees byte-identical inputs and this script needs no app code. Generates once per PR,
greedy, no constrained decoding, no retries (model metric, hard rule 10). Writes replies in
the eval harness format plus a `.meta.json` naming the model, the settings and the pack's
SHA-256; scoring happens on the laptop.

Usage (inside the sbatch job):
    python training/baselines/vllm_replies.py --name qwen3-1.7b --pack <pack.jsonl> \
        --out <replies.jsonl> --model <hf repo> --revision <sha> --thinking off \
        --max-tokens 1024 --max-model-len 16384 [--lora-dir <run output dir>]
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
MAX_LORA_RANK = 16  # training/sft/configs: r = 16


def checkpoints(run_dir: Path) -> list[Path]:
    """A training run's adapters in step order, with `final` (after the last step) last."""
    steps = sorted(run_dir.glob("checkpoint-*"), key=lambda d: int(d.name.split("-")[1]))
    final = run_dir / "final"
    return steps + ([final] if final.is_dir() else [])


def adapter_info(path: Path) -> dict:
    weights = path / "adapter_model.safetensors"
    return {
        "checkpoint": path.name,
        "path": str(path),
        "sha256": "sha256:" + hashlib.sha256(weights.read_bytes()).hexdigest(),
    }


def code_commit() -> str | None:
    """The cluster copy has no .git; the laptop writes the commit it was copied from."""
    path = REPO_ROOT / "COMMIT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def main() -> None:
    from vllm import LLM, SamplingParams
    from vllm.lora.request import LoRARequest

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
    p.add_argument("--shard", help="I/N: only every N-th PR starting at I (0-based), e.g. 0/4")
    p.add_argument("--lora-dir", type=Path, help="score every LoRA checkpoint in this run folder")
    args = p.parse_args()
    adapters = checkpoints(args.lora_dir) if args.lora_dir else [None]
    if args.lora_dir and not adapters[0]:
        raise SystemExit(f"no checkpoints in {args.lora_dir}")

    raw = args.pack.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if args.shard:
        index, count = map(int, args.shard.split("/"))
        rows = rows[index::count]
    if args.limit:
        rows = rows[: args.limit]

    start = time.perf_counter()
    extra = {"limit_mm_per_prompt": {"image": 0, "video": 0}} if args.text_only else {}
    if args.lora_dir:
        extra |= {"enable_lora": True, "max_lora_rank": MAX_LORA_RANK, "max_loras": 1}
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
    import torch

    for lora_id, adapter in enumerate(adapters, start=1):
        # One replies file per checkpoint: <out stem>_<checkpoint>.jsonl
        out = args.out.with_name(f"{args.out.stem}_{adapter.name}.jsonl") if adapter else args.out
        start = time.perf_counter()
        outs = llm.chat(
            [r["messages"] for r in rows],
            params,
            chat_template_kwargs={"enable_thinking": args.thinking == "on"},
            lora_request=LoRARequest(adapter.name, lora_id, str(adapter)) if adapter else None,
        )
        gen_s = time.perf_counter() - start

        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as f:
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

        meta = {
            "name": f"{args.name}-{adapter.name}" if adapter else args.name,
            "model": {"repo": args.model, "revision": args.revision},
            "pack_file": args.pack.name,
            "pack_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "prs": len(rows),
            "adapter": adapter_info(adapter) if adapter else None,
            "trial_limit": args.limit,
            "shard": args.shard,
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
        meta_path = out.with_name(out.name.removesuffix(".jsonl") + ".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"load {load_s:.0f}s, generate {gen_s:.0f}s for {len(rows)} PRs; wrote {out}")


if __name__ == "__main__":
    main()
