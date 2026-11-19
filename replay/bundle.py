"""Write a replay bundle: the PRs to replay, as of their open time only (hard rule 11).

    uv run --group data python -m replay.bundle --split val --every 10 --out PATH
        --keys data/processed/prompts_val_s500.jsonl        (the pilot: 50 PRs)
    uv run --group data python -m replay.bundle --split test --out PATH

Each line is `prpilot_core.github.replay.bundle_record`: the dataset row's open-time fields and
its open-time candidate lists, nothing else. Lines are in the order the PRs were opened (the
order the sender sends them). `--keys` limits the bundle to the PRs of a prompt pack, so a
pilot can reuse the seeded sample the offline evaluation used; `--every N` then keeps every
Nth of them in the pack's order (the pack is sorted by repo, so this spreads over all repos)
and `--first N` the first N. Bundles live in data/processed/replay/ (not committed) and go to
the replay bucket, where the worker reads them.
"""

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

from prpilot_core.github.replay import bundle_record
from prpilot_data.records import REPO_ROOT, read_jsonl

PROCESSED = REPO_ROOT / "data" / "processed"


def build(rows: list[dict], candidates: list[dict], keys: list[tuple[str, int]] | None = None,
          every: int = 1, first: int | None = None) -> list[dict]:  # fmt: skip
    cands = {(c["repo"], int(c["number"])): c for c in candidates}
    by_key = {(r["repo"], int(r["number"])): r for r in rows}
    picked = list(keys) if keys is not None else sorted(by_key)
    missing = [k for k in picked if k not in by_key or k not in cands]
    if missing:
        raise SystemExit(f"{len(missing)} PRs have no row or candidates, e.g. {missing[:3]}")
    picked = picked[::every]
    if first is not None:
        picked = picked[:first]
    records = [bundle_record(by_key[k], cands[k]) for k in picked]
    return sorted(records, key=opened)


def opened(record: dict) -> tuple:
    pr = record["pr"]
    return pr["created_at"], pr["repo"], pr["number"]


def write(records: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii keeps every line separator inside PR text escaped: one record per line.
    out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=["val", "test"], required=True)
    ap.add_argument("--keys", type=Path, help="a prompt pack whose PRs (in order) to use")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--first", type=int)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = pq.read_table(PROCESSED / f"prpilot_{a.split}.parquet").to_pylist()
    candidates = [c for c in read_jsonl(PROCESSED / "candidates.jsonl") if c["split"] == a.split]
    keys = [(p["repo"], int(p["number"])) for p in read_jsonl(a.keys)] if a.keys else None
    records = build(rows, candidates, keys, a.every, a.first)
    write(records, a.out)
    repos = sorted({r["pr"]["repo"] for r in records})
    print(f"{len(records)} PRs from {a.split} ({', '.join(repos)}) -> {a.out}")


if __name__ == "__main__":
    main()
