"""Eval harness command line.

uv run --project eval python -m prpilot_eval score --pack P --targets T --replies R --out O
    Scores one model's raw replies (JSONL: repo, number, text, and optionally finish_reason,
    prompt_tokens, output_tokens, latency_s; a `<replies>.meta.json` beside it says which
    model and which prompt pack produced them). Refuses replies made from a different pack.
    Writes the report to O and per-PR outcomes to O with `.records.jsonl`.

uv run --project eval python -m prpilot_eval compare --a A.records.jsonl --b B.records.jsonl
    Exact McNemar test on maintainer match between two scored runs (same PRs).
"""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from prpilot_eval import metrics, validate
from prpilot_eval.stats import mcnemar_exact

ROOT = Path(__file__).resolve().parents[2]


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [json.loads(line) for line in f.read().split("\n") if line.strip()]


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def git_info() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    status = run("status", "--porcelain")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status)}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[int(q * (len(values) - 1))], 3)


def score(pack_path: Path, targets_path: Path, replies_path: Path) -> tuple[dict, list[dict]]:
    meta_path = replies_path.with_name(replies_path.name.removesuffix(".jsonl") + ".meta.json")
    runner = json.loads(meta_path.read_text(encoding="utf-8"))
    pack_sha = sha256(pack_path)
    if runner.get("pack_sha256") != pack_sha:
        raise SystemExit(f"replies were made from pack {runner.get('pack_sha256')}, not {pack_sha}")
    pack = read_jsonl(pack_path)
    targets = {(t["repo"], t["number"]): t for t in read_jsonl(targets_path)}
    replies = {(r["repo"], r["number"]): r for r in read_jsonl(replies_path)}
    records, errors, finish = [], [], Counter()
    for p in pack:
        key = (p["repo"], p["number"])
        reply = replies.get(key)
        if reply is None:
            parsed, error = None, "missing: no reply"
        else:
            parsed, error = validate.check(reply["text"])
            finish[reply.get("finish_reason") or "unknown"] += 1
        errors.append(error)
        rec = metrics.per_pr(targets[key], parsed)
        rec["error"] = error
        records.append(rec)
    got = [r for r in replies.values()]
    report = {
        "name": runner["name"],
        "runner": runner,
        "scored_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_info(),
        "python": platform.python_version(),
        "packages": {"jsonschema": version("jsonschema")},
        "inputs": {
            "pack": {"file": pack_path.name, "sha256": pack_sha},
            "targets": {"file": targets_path.name, "sha256": sha256(targets_path)},
            "replies": {"file": replies_path.name, "sha256": sha256(replies_path)},
        },
        "prs": len(pack),
        "metrics": metrics.summarize(records),
        "per_type": metrics.per_type(records),
        "breakdowns": metrics.breakdowns(records),
        "invalid_reasons": metrics.invalid_reasons(errors),
        "finish_reasons": dict(finish),
        "tokens_per_pr": {
            k: round(statistics.mean(r[k] for r in got if r.get(k) is not None), 1)
            if any(r.get(k) is not None for r in got)
            else None
            for k in ("prompt_tokens", "output_tokens")
        },
        "latency_s": {
            "p50": percentile([r["latency_s"] for r in got if r.get("latency_s") is not None], 0.5),
            "p95": percentile(
                [r["latency_s"] for r in got if r.get("latency_s") is not None], 0.95
            ),
        },
    }
    return report, records


def compare(a_path: Path, b_path: Path) -> dict:
    a = {(r["repo"], r["number"]): r for r in read_jsonl(a_path)}
    b = {(r["repo"], r["number"]): r for r in read_jsonl(b_path)}
    if a.keys() != b.keys():
        raise SystemExit("the two runs scored different PRs")
    keys = sorted(k for k in a if a[k]["type_truth"] is not None)
    return {
        "a": a_path.name,
        "b": b_path.name,
        "metric": "maintainer_match",
        **mcnemar_exact([a[k]["type_correct"] for k in keys], [b[k]["type_correct"] for k in keys]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m prpilot_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--pack", type=Path, required=True)
    s.add_argument("--targets", type=Path, required=True)
    s.add_argument("--replies", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("--a", type=Path, required=True)
    c.add_argument("--b", type=Path, required=True)
    args = parser.parse_args()
    if args.cmd == "score":
        report, records = score(args.pack, args.targets, args.replies)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        rec_path = args.out.with_name(args.out.name.removesuffix(".json") + ".records.jsonl")
        with rec_path.open("w", encoding="utf-8", newline="") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)
        m = report["metrics"]
        print(
            f"{report['name']}: maintainer match {m['maintainer_match']['value']} "
            f"(n={m['maintainer_match']['n']}), valid JSON {m['json_validity']['value']}, "
            f"reviewer@3 {m['reviewer_recall_at_3']['value']}, "
            f"issue@5 {m['issue_recall_at_5']['value']}, "
            f"label F1 {m['label_micro_f1']['value']}"
        )
        print(f"wrote {args.out} and {rec_path}")
    else:
        print(json.dumps(compare(args.a, args.b), indent=2))


if __name__ == "__main__":
    main()
