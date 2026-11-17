"""PromptGuard 2 scores for every model-visible field of every PR in a file (Explorer GPU).

Input lines carry the PR fields (`pr`: title, body, author, base_branch, changed_files,
diff), as in data/processed/redteam_<split>.jsonl and prfields_<split>.jsonl. Each field is
scored exactly as the screening step does (prpilot_core.screening.screen.guard_fields, max
over overlapping 512-token windows), so a threshold chosen from these scores means the same
thing in the app. The threshold itself is applied later, on the laptop.

    python training/screening/guard_scores.py --input <file.jsonl> --out <scores.jsonl> \
        --local-dir <copy of the pinned PromptGuard snapshot>
"""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from prpilot_core.agents.triage_format import PRContext
from prpilot_core.screening.prompt_guard import PROMPT_GUARD_86M, PromptGuard
from prpilot_core.screening.screen import guard_fields

REPO_ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def main() -> None:
    import torch

    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--local-dir", type=Path, required=True)
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--limit", type=int, help="trial: only the first N lines")
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    start = time.perf_counter()
    guard = PromptGuard(
        PROMPT_GUARD_86M, local_dir=str(args.local_dir), device=device, batch_size=args.batch_size
    )
    load_s = time.perf_counter() - start
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    if args.limit:
        rows = rows[: args.limit]

    start, windows = time.perf_counter(), 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        for i, row in enumerate(rows):
            pr = row["pr"]
            fields = guard_fields(PRContext(**{**pr, "changed_files": tuple(pr["changed_files"])}))
            scores, n_windows = {}, {}
            for name, text in fields.items():
                if not text:
                    scores[name], n_windows[name] = 0.0, 0
                    continue
                r = guard.score(text)
                scores[name], n_windows[name] = round(r.score, 6), r.n_windows
                windows += r.n_windows
            line = {"i": i, "repo": row["repo"], "number": row["number"], "scores": scores,
                    "windows": n_windows}  # fmt: skip
            if "redteam" in row:
                line["redteam"] = row["redteam"]
            f.write(json.dumps(line) + "\n")
            if (i + 1) % 1000 == 0:
                print(f"{i + 1}/{len(rows)} PRs, {windows} windows", flush=True)
    score_s = time.perf_counter() - start

    meta = {
        "model": {"repo": PROMPT_GUARD_86M.repo, "revision": PROMPT_GUARD_86M.revision},
        "weights_sha256": sha256(args.local_dir / "model.safetensors"),
        "input": {"file": args.input.name, "sha256": sha256(args.input), "rows": len(rows)},
        "trial_limit": args.limit,
        "settings": {
            "window_tokens": 512,
            "stride_tokens": guard.stride,
            "fields": list(fields),
            "aggregate": "max over windows",
        },  # fmt: skip
        "windows": windows,
        "timing_s": {
            "load": round(load_s, 1),
            "score": round(score_s, 1),
            "per_pr": round(score_s / len(rows), 4),
        },  # fmt: skip
        "device": torch.cuda.get_device_name() if device == "cuda" else "cpu",
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "code_commit": (REPO_ROOT / "COMMIT").read_text().strip()
        if (REPO_ROOT / "COMMIT").exists()
        else None,
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "packages": {k: version(k) for k in ("torch", "transformers")},
    }
    meta_path = args.out.with_name(args.out.name.removesuffix(".jsonl") + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"scored {len(rows)} PRs ({windows} windows) in {score_s:.0f}s; wrote {args.out}")


if __name__ == "__main__":
    main()
