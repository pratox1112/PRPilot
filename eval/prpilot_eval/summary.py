"""One file with every baseline: headline metrics, system reliability, cost, paired tests.

Reads scored reports (`score --out`) and the `.records.jsonl` beside each. Reports are grouped
by the PR set they were scored on (e.g. all of val, the 500-PR sample); McNemar tests are run
for every pair inside a group, never across groups.
"""

import hashlib
import json
from itertools import combinations
from pathlib import Path

from prpilot_eval.stats import mcnemar_exact

HEADLINE = (
    "maintainer_match",
    "type_macro_f1",
    "label_micro_f1",
    "reviewer_recall_at_3",
    "reviewer_recall_at_3_reviewed_only",
    "issue_recall_at_5",
    "issue_recall_at_5_named_at_open",
    "issue_recall_at_5_not_named",
    "json_validity",
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _records(report_path: Path) -> dict:
    path = report_path.with_name(report_path.name.removesuffix(".json") + ".records.jsonl")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {(r["repo"], r["number"]): r for r in rows}


def system_stats(runner: dict) -> dict | None:
    """Retry and rescue rates from an agent run, over every PR the run answered (a run made on
    all of val keeps its full-val rates when scored on a sample). Exact when every failed PR used
    all its requests, i.e. the run's only errors are 'retries exhausted' (`exact`)."""
    agent = runner.get("agent")
    if not agent:
        return None
    prs = runner["prs"]
    first_ok = agent["requests_per_pr"].get("1", 0)
    retried = prs - first_ok
    valid = agent["valid_after_retries"]
    only_exhausted = set(agent["errors"]) <= {"UnexpectedModelBehavior"}
    return {
        "valid_after_retries": {"value": round(valid / prs, 4), "n": prs},
        "valid_first_attempt_both_layers": {"value": round(first_ok / prs, 4), "n": prs},
        "retry_rate": {"value": round(retried / prs, 4), "n": prs},
        "rescue_rate": {
            "value": round((valid - first_ok) / retried, 4) if retried else None,
            "n": retried,
        },
        "requests_per_pr": agent["requests_per_pr"],
        "over_run_prs": prs,
        "exact": only_exhausted,
    }


def cost(runner: dict) -> dict | None:
    """Per-PR cost over the whole run (see `system_stats` for why not the scored subset)."""
    prs = runner.get("prs")
    if "cost" in runner:
        c = runner["cost"]
        return {
            "usd_per_pr": round(c["usd_estimated"] / prs, 6),
            "usd_total": c["usd_estimated"],
            "method": "measured tokens (retries included) x published Vertex AI price",
            "price_per_mtok": c["price_per_mtok"],
        }
    timing = runner.get("timing_s") or {}
    if "per_pr" in timing and runner.get("gpu"):
        return {
            "gpu_seconds_per_pr": timing["per_pr"],
            "gpu": runner["gpu"],
            "method": "batch generation time on Explorer / PRs; not a serving cost",
        }
    if "per_pr" in timing:
        return {
            "wall_seconds_per_pr": timing["per_pr"],
            "method": "agent run time on Explorer (1 GPU, concurrent requests) / PRs",
        }
    return None


def entry(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    runner, prs = report["runner"], report["prs"]
    return {
        "name": report["name"],
        "report": {"file": report_path.name, "sha256": _sha256(report_path)},
        "scored_at_commit": report["git"]["commit"],
        "model": runner.get("model"),
        "mode": "system" if runner.get("agent") else "model",
        "settings": runner.get("settings"),
        "prs": prs,
        "metrics": {k: report["metrics"][k] for k in HEADLINE},
        "risk_auroc_reported_only": report["metrics"]["risk_auroc"],
        "invalid_reasons": report["invalid_reasons"],
        "system": system_stats(runner),
        "cost": cost(runner),
        "tokens_per_pr": report["tokens_per_pr"],
        "latency_s": report["latency_s"],
    }


def group(name: str, report_paths: list[Path]) -> dict:
    entries = [entry(p) for p in report_paths]
    records = {e["name"]: _records(p) for e, p in zip(entries, report_paths, strict=True)}
    sets = {frozenset(r) for r in records.values()}
    if len(sets) != 1:
        raise SystemExit(f"group {name}: reports were scored on different PRs")
    tests = []
    for a, b in combinations(records, 2):
        ra, rb = records[a], records[b]
        keys = sorted(k for k in ra if ra[k]["type_truth"] is not None)
        tests.append(
            {
                "a": a,
                "b": b,
                "metric": "maintainer_match",
                **mcnemar_exact(
                    [ra[k]["type_correct"] for k in keys], [rb[k]["type_correct"] for k in keys]
                ),
            }
        )
    return {"prs": len(next(iter(sets))), "baselines": entries, "mcnemar": tests}
