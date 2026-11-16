"""Join a sharded run (`vllm_replies.py --shard I/N`) into one replies file for the eval harness.

The shards must come from the same pack, model and settings, cover it exactly once, and
together number N of N. Lines are written in pack order. The joined .meta.json is the first
shard's, with prs set to the total, generate time summed over shards (GPU-seconds), and a
`shards` list naming each source file, its hash and its Slurm job.

Usage:
    uv run python scripts/join_shards.py --pack data/processed/prompts_test.jsonl \
        --name teacher_test data/processed/replies/teacher_test_s*of4_*.jsonl
"""

import argparse
import hashlib
import json
from pathlib import Path

REPLIES = Path(__file__).resolve().parents[1] / "data" / "processed" / "replies"
SAME = ("model", "pack_sha256", "settings")


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--name", required=True, help="output: replies/<name>.jsonl")
    p.add_argument("shards", type=Path, nargs="+")
    args = p.parse_args()

    metas = [json.loads(s.with_name(s.stem + ".meta.json").read_text("utf-8")) for s in args.shards]
    first = metas[0]
    for s, m in zip(args.shards, metas, strict=True):
        if any(m[k] != first[k] for k in SAME) or m.get("trial_limit"):
            raise SystemExit(f"{s.name}: different model, pack or settings, or a trial run")
    if sha256(args.pack) != first["pack_sha256"]:
        raise SystemExit("the shards were made from another pack")
    counts = {int(m["shard"].split("/")[1]) for m in metas}
    indexes = sorted(int(m["shard"].split("/")[0]) for m in metas)
    if len(counts) != 1 or indexes != list(range(counts.pop())):
        raise SystemExit(f"shards {[m['shard'] for m in metas]} are not 0..N-1 of N")

    replies = {}
    for s in args.shards:
        for line in s.read_text("utf-8").splitlines():
            r = json.loads(line)
            key = (r["repo"], r["number"])
            if key in replies:
                raise SystemExit(f"{s.name}: {key} answered twice")
            replies[key] = line
    pack = [json.loads(line) for line in args.pack.read_text("utf-8").splitlines() if line]
    missing = [(r["repo"], r["number"]) for r in pack if (r["repo"], r["number"]) not in replies]
    if missing or len(replies) != len(pack):
        raise SystemExit(f"{len(missing)} PRs missing, {len(replies) - len(pack)} extra")

    out = REPLIES / f"{args.name}.jsonl"
    out.write_text("".join(replies[(r["repo"], r["number"])] + "\n" for r in pack), "utf-8")
    generate = sum(m["timing_s"]["generate"] for m in metas)
    meta = {
        **first,
        "name": args.name,
        "prs": len(pack),
        "shard": None,
        "timing_s": {
            "load": first["timing_s"]["load"],
            "generate": round(generate, 1),
            "per_pr": round(generate / len(pack), 3),
        },
        "slurm_job_id": None,
        "shards": [
            {
                "file": s.name,
                "sha256": sha256(s),
                "shard": m["shard"],
                "slurm_job_id": m["slurm_job_id"],
                "code_commit": m["code_commit"],
                "timing_s": m["timing_s"],
            }
            for s, m in zip(args.shards, metas, strict=True)
        ],
    }
    out.with_name(f"{args.name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    print(f"joined {len(args.shards)} shards, {len(pack)} PRs; wrote {out}")


if __name__ == "__main__":
    main()
