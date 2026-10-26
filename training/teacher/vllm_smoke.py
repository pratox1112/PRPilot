"""Teacher smoke test on Explorer: load the pinned teacher in vLLM on one H200 and triage the
three synthetic PRs used in task 0.8, with thinking off and on.

Checks that the model loads, how long it takes, and whether its first reply parses against
the static schema and the repo rules (model metric, no retries, no constrained decoding).
Not the 0.6 agreement measurement; that runs on 200 real train-window PRs.

Usage (inside the sbatch job): python training/teacher/vllm_smoke.py --out <file.json>
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "scripts" / "feasibility")]

from pydantic import ValidationError  # noqa: E402
from synthetic_prs import RULES, SAMPLES  # noqa: E402

from prpilot_core.agents.triage_format import (  # noqa: E402
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_static,
    repo_rule_errors,
)
from prpilot_core.deps import TriageDeps  # noqa: E402

MAX_MODEL_LEN = 16384


def git_info() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def final_answer(text: str) -> str:
    """Drop a leading <think>...</think> block if the model emitted one."""
    return text.split("</think>", 1)[1] if "</think>" in text else text


def check(text: str, author: str) -> dict:
    try:
        out = parse_static(final_answer(text))
    except ValidationError as e:
        return {"valid_static": False, "valid_with_repo_rules": False, "error": str(e)[:400]}
    errors = repo_rule_errors(out, TriageDeps(rules=RULES, pr_author=author))
    return {
        "valid_static": True,
        "valid_with_repo_rules": not errors,
        "error": "; ".join(errors),
        "type": out.type.value,
    }


def main() -> None:
    from vllm import LLM, SamplingParams

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    repo, revision = os.environ["TEACHER_REPO"], os.environ["TEACHER_REVISION"]
    start = time.perf_counter()
    llm = LLM(
        model=repo,
        revision=revision,
        max_model_len=MAX_MODEL_LEN,
        gpu_memory_utilization=0.92,
        limit_mm_per_prompt={"image": 0, "video": 0},
        seed=0,
    )
    load_s = time.perf_counter() - start

    conversations = [
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(pr, RULES)},
        ]
        for pr in SAMPLES
    ]
    runs = {}
    for thinking, max_tokens in [(False, 1024), (True, 8192)]:
        params = SamplingParams(temperature=0.0, max_tokens=max_tokens, seed=0)
        start = time.perf_counter()
        outputs = llm.chat(
            conversations, params, chat_template_kwargs={"enable_thinking": thinking}
        )
        elapsed = time.perf_counter() - start
        rows = []
        for pr, o in zip(SAMPLES, outputs, strict=True):
            text = o.outputs[0].text
            rows.append(
                {
                    "title": pr.title,
                    "prompt_tokens": len(o.prompt_token_ids),
                    "output_tokens": len(o.outputs[0].token_ids),
                    "finish_reason": o.outputs[0].finish_reason,
                    "has_think_block": "</think>" in text,
                    **check(text, pr.author),
                    "raw_text": text,
                }
            )
        valid = sum(r["valid_static"] for r in rows)
        runs["thinking_on" if thinking else "thinking_off"] = {
            "batch_seconds": round(elapsed, 1),
            "first_attempt_valid_static": valid,
            "first_attempt_valid_with_repo_rules": sum(r["valid_with_repo_rules"] for r in rows),
            "n": len(rows),
            "rows": rows,
        }
        types = [r.get("type") for r in rows]
        print(f"thinking={thinking}: valid {valid}/{len(rows)}, {elapsed:.1f}s, types={types}")

    payload = {
        "task": "0.6 (teacher smoke test)",
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_info(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "packages": {p: version(p) for p in ["vllm", "torch", "transformers"]},
        "model": {"repo": repo, "revision": revision},
        "settings": {
            "max_model_len": MAX_MODEL_LEN,
            "gpu_memory_utilization": 0.92,
            "temperature": 0.0,
            "constrained_decoding": False,
        },
        "load_seconds": round(load_s, 1),
        "dataset": {
            "name": "synthetic_sample_prs",
            "n": len(SAMPLES),
            "samples": [asdict(p) for p in SAMPLES],
        },
        "runs": runs,
        "note": "Smoke test on 3 synthetic PRs; not the 0.6 agreement metric.",
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"load {load_s:.0f}s; wrote {args.out}")


if __name__ == "__main__":
    main()
