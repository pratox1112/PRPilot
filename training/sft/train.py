"""Phase 3: LoRA fine-tuning of the student on the agreement-filtered teacher data (Explorer).

bf16 base weights + LoRA adapters (PEFT), Hugging Face Trainer, loss on the answer only
(format.encode masks the prompt). Checkpoints (adapter only) every `save_steps`; with
--resume-if-exists a job that hit its time limit continues from the last one, in the same
MLflow run. Every checkpoint is later scored on validation; the best one is chosen there.

    python training/sft/train.py --config training/sft/configs/base.toml \
        --train <sft_train.jsonl> --val <sft_val.jsonl> --out <dir> --resume-if-exists
"""

import argparse
import hashlib
import json
import os
import random
import tomllib
from importlib.metadata import version
from pathlib import Path

import mlflow
import torch
from format import IGNORE, encode
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
from transformers.trainer_utils import get_last_checkpoint

REPO_ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, tokenizer, max_len: int, limit: int | None = None) -> tuple[list, int]:
    rows, too_long = [], 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            ids, labels = encode(tokenizer, json.loads(line)["messages"])
            if len(ids) > max_len:
                too_long += 1  # dropped, never truncated: a cut answer would teach bad JSON
                continue
            rows.append({"input_ids": ids, "labels": labels})
            if limit and len(rows) >= limit:
                break
    return rows, too_long


def collate(pad_id: int):
    def fn(batch: list[dict]) -> dict:
        width = max(len(b["input_ids"]) for b in batch)
        ids, labels, mask = [], [], []
        for b in batch:
            pad = width - len(b["input_ids"])
            ids.append(b["input_ids"] + [pad_id] * pad)
            labels.append(b["labels"] + [IGNORE] * pad)
            mask.append([1] * len(b["input_ids"]) + [0] * pad)
        return {
            "input_ids": torch.tensor(ids),
            "labels": torch.tensor(labels),
            "attention_mask": torch.tensor(mask),
        }

    return fn


def code_commit() -> str | None:
    path = REPO_ROOT / "COMMIT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--train", type=Path, required=True)
    p.add_argument("--val", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resume-if-exists", action="store_true")
    p.add_argument("--max-steps", type=int, default=-1, help="trial: stop after N steps")
    args = p.parse_args()
    cfg = tomllib.loads(args.config.read_text(encoding="utf-8"))
    m, lora, t = cfg["model"], cfg["lora"], cfg["train"]
    random.seed(t["seed"])
    torch.manual_seed(t["seed"])

    tok = AutoTokenizer.from_pretrained(m["repo"], revision=m["revision"])
    train, train_too_long = load(args.train, tok, t["max_len"])
    val, _ = load(args.val, tok, t["max_len"], limit=t["eval_examples"])
    print(f"train {len(train)} examples ({train_too_long} over {t['max_len']} tokens dropped)")

    model = AutoModelForCausalLM.from_pretrained(
        m["repo"], revision=m["revision"], dtype=torch.bfloat16, attn_implementation="sdpa"
    )
    if t["gradient_checkpointing"]:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    model = get_peft_model(
        model,
        LoraConfig(
            r=lora["r"],
            lora_alpha=lora["alpha"],
            lora_dropout=lora["dropout"],
            target_modules=lora["target_modules"],
            task_type="CAUSAL_LM",
        ),
    )
    model.print_trainable_parameters()

    args.out.mkdir(parents=True, exist_ok=True)
    resume = get_last_checkpoint(str(args.out)) if args.resume_if_exists else None
    run_id_file = args.out / "mlflow_run_id.txt"
    run_id = run_id_file.read_text().strip() if resume and run_id_file.exists() else None
    mlflow.set_experiment("prpilot-student")
    with mlflow.start_run(run_id=run_id, run_name=args.out.name) as run:
        run_id_file.write_text(run.info.run_id)
        if run_id is None:
            mlflow.log_params(
                {
                    "base_repo": m["repo"],
                    "base_revision": m["revision"],
                    **{f"lora_{k}": v for k, v in lora.items() if k != "target_modules"},
                    "lora_target_modules": ",".join(lora["target_modules"]),
                    **{f"train_{k}": v for k, v in t.items()},
                    "train_examples": len(train),
                    "train_dropped_too_long": train_too_long,
                    "val_examples_for_loss": len(val),
                }
            )
            mlflow.set_tags(
                {
                    "train_data_sha256": sha256(args.train),
                    "val_data_sha256": sha256(args.val),
                    "config_sha256": sha256(args.config),
                    "code_commit": code_commit() or "unknown",
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
                    **{f"pkg_{k}": version(k) for k in ("torch", "transformers", "peft")},
                }
            )
        trainer = Trainer(
            model=model,
            args=TrainingArguments(
                output_dir=str(args.out),
                num_train_epochs=t["epochs"],
                max_steps=args.max_steps,
                per_device_train_batch_size=t["per_device_batch"],
                per_device_eval_batch_size=t["per_device_batch"],
                gradient_accumulation_steps=t["grad_accum"],
                learning_rate=t["learning_rate"],
                lr_scheduler_type=t["scheduler"],
                warmup_steps=t["warmup_ratio"],  # transformers 5.x: a float in [0, 1) is a ratio
                weight_decay=t["weight_decay"],
                bf16=True,
                logging_steps=10,
                eval_strategy="steps",
                eval_steps=t["eval_steps"],
                save_strategy="steps",
                save_steps=t["save_steps"],
                save_total_limit=None,
                seed=t["seed"],
                data_seed=t["seed"],
                report_to=["mlflow"],
                remove_unused_columns=False,
                dataloader_num_workers=2,
            ),
            train_dataset=train,
            eval_dataset=val,
            data_collator=collate(tok.pad_token_id),
        )
        trainer.train(resume_from_checkpoint=resume)
        final = args.out / "final"
        trainer.save_model(str(final))
        tok.save_pretrained(str(final))
        print(f"saved {final}")


if __name__ == "__main__":
    main()
