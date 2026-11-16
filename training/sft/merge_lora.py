"""Merge a LoRA checkpoint into the base model (bf16), for the GGUF export (roadmap §6.3).

Saves the merged weights with the base model's own tokenizer and generation config, so the
chat template inside the GGUF is the one training and vLLM used. Writes merge_meta.json with
the base revision and the adapter's weights hash.

    python training/sft/merge_lora.py --adapter <checkpoint dir> --out <merged dir> \
        --model Qwen/Qwen3-1.7B --revision <sha>
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--revision", required=True)
    args = p.parse_args()

    base = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, dtype=torch.bfloat16
    )
    merged = PeftModel.from_pretrained(base, str(args.adapter)).merge_and_unload()
    args.out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(args.out), safe_serialization=True)
    AutoTokenizer.from_pretrained(args.model, revision=args.revision).save_pretrained(str(args.out))
    GenerationConfig.from_pretrained(args.model, revision=args.revision).save_pretrained(
        str(args.out)
    )
    weights = (args.adapter / "adapter_model.safetensors").read_bytes()
    meta = {
        "base": {"repo": args.model, "revision": args.revision},
        "adapter": {
            "path": str(args.adapter),
            "sha256": "sha256:" + hashlib.sha256(weights).hexdigest(),
        },
        "dtype": "bfloat16",
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("torch", "transformers", "peft")},
    }
    (args.out / "merge_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"merged {args.adapter} into {args.model}@{args.revision[:8]}; wrote {args.out}")


if __name__ == "__main__":
    main()
