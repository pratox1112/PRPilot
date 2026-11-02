"""Task 0.6, step 2 (Explorer): run the teacher on the 200 sampled train-window PRs.

Uses the shared prompt from prpilot_core.agents.triage_format, so the teacher sees exactly
what the student will. Runs twice: thinking off and thinking on. Writes raw replies only;
scoring happens on the laptop (scripts/feasibility/score_agreement.py), so nothing about
the maintainer labels is used here.

Usage (inside the sbatch job):
    python training/teacher/teacher_agreement.py --inputs <inputs.jsonl> --out <outputs.jsonl>
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from prpilot_core.agents.triage_format import (  # noqa: E402
    SYSTEM_PROMPT,
    PRContext,
    build_user_prompt,
)
from prpilot_core.deps import RepoRules  # noqa: E402

MAX_MODEL_LEN = 32768
MODES = {"thinking_off": (False, 1024), "thinking_on": (True, 8192)}


def git_info() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def conversation(row: dict) -> list[dict]:
    c = row["context"]
    pr = PRContext(
        title=c["title"],
        body=c["body"],
        author=c["author"],
        base_branch=c["base_branch"],
        changed_files=tuple(c["changed_files"]),
        diff=c["diff"],
    )
    # Only the type is scored in 0.6; reviewers and issues are left empty on purpose.
    rules = RepoRules(labels=frozenset(row["repo_labels"]), eligible_reviewers=frozenset())
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(pr, rules)},
    ]


def main() -> None:
    from vllm import LLM, SamplingParams

    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    raw = Path(args.inputs).read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
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
    convs = [conversation(r) for r in rows]

    records, timings = {(r["repo"], r["number"]): {} for r in rows}, {}
    for mode, (thinking, max_tokens) in MODES.items():
        params = SamplingParams(temperature=0.0, max_tokens=max_tokens, seed=0)
        start = time.perf_counter()
        outs = llm.chat(convs, params, chat_template_kwargs={"enable_thinking": thinking})
        timings[mode] = round(time.perf_counter() - start, 1)
        for row, o in zip(rows, outs, strict=True):
            records[(row["repo"], row["number"])][mode] = {
                "text": o.outputs[0].text,
                "finish_reason": o.outputs[0].finish_reason,
                "prompt_tokens": len(o.prompt_token_ids),
                "output_tokens": len(o.outputs[0].token_ids),
            }
        print(f"{mode}: {len(rows)} PRs in {timings[mode]}s")

    meta = {
        "task": "0.6 (teacher outputs)",
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_info(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "packages": {p: version(p) for p in ["vllm", "torch", "transformers"]},
        "model": {"repo": repo, "revision": revision},
        "settings": {
            "max_model_len": MAX_MODEL_LEN,
            "temperature": 0.0,
            "modes": {m: {"thinking": t, "max_tokens": n} for m, (t, n) in MODES.items()},
            "constrained_decoding": False,
        },
        "inputs_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "load_seconds": round(load_s, 1),
        "mode_seconds": timings,
    }
    with Path(args.out).open("w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta}) + "\n")
        for (r, n), modes in records.items():
            f.write(json.dumps({"repo": r, "number": n, **modes}, ensure_ascii=False) + "\n")
    print(f"load {load_s:.0f}s; wrote {args.out}")


if __name__ == "__main__":
    main()
