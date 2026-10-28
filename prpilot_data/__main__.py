"""Command line for the data pipeline.

uv run --group data python -m prpilot_data gharchive-estimate
    Dry-runs the sample and full GH Archive queries; prints bytes and cost. Free.

uv run --group data python -m prpilot_data gharchive-sample --budget-gib N
    Runs the 3-day sample query (only if its estimate is <= N GiB) and reports how many
    `opened` events still carry the open-time fields.
"""

import argparse
import json
import os
import tomllib

from dotenv import load_dotenv

from prpilot_data import gharchive
from prpilot_data.records import REPO_ROOT, run_metadata, write_result

REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
RAW_DIR = REPO_ROOT / "data" / "raw" / "gharchive"


def selected_repos() -> list[str]:
    cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))["repos"]
    return sorted(r for r, c in cfg.items() if c.get("status") == "selected")


def client_and_config():
    from google.cloud import bigquery

    load_dotenv(REPO_ROOT / ".env")
    project = os.getenv("GCP_PROJECT")
    if not project:
        raise SystemExit("GCP_PROJECT is not set in .env")
    params = [bigquery.ArrayQueryParameter("repos", "STRING", selected_repos())]
    return bigquery, bigquery.Client(project=project), params


def estimate(bigquery, client, params, sql: str) -> gharchive.Estimate:
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params)
    return gharchive.Estimate(client.query(sql, job_config=cfg).total_bytes_processed)


def cmd_estimate() -> None:
    bigquery, client, params = client_and_config()
    for name, sql in [
        ("sample (3 days)", gharchive.sample_sql()),
        ("full (2023-01..2026-03)", gharchive.full_sql()),
    ]:
        e = estimate(bigquery, client, params, sql)
        print(
            f"{name}: {e.gib:,.1f} GiB scanned, ~${e.usd():,.2f} at on-demand list price "
            "(first 1 TiB per month is free)"
        )


def cmd_sample(budget_gib: float) -> None:
    bigquery, client, params = client_and_config()
    sql = gharchive.sample_sql()
    e = estimate(bigquery, client, params, sql)
    cfg = bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=gharchive.byte_cap(e, budget_gib)
    )
    job = client.query(sql, job_config=cfg)
    rows = [dict(r) for r in job.result()]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / "sample_opened.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    summary = gharchive.completeness(rows)
    path = write_result(
        "data_gharchive_sample",
        {
            "step": "Phase 1 step 2: GH Archive payload completeness",
            "meta": run_metadata(["google-cloud-bigquery"]),
            "sample_days": gharchive.SAMPLE_DAYS,
            "repos": selected_repos(),
            "bytes_estimated": e.bytes_processed,
            "bytes_billed": job.total_bytes_billed,
            "opened_events": len(rows),
            "completeness_by_day": summary,
            "raw_file": str(raw_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    )
    for day, s in summary.items():
        print(day, s)
    print(f"billed {job.total_bytes_billed / 2**30:.1f} GiB; wrote {path} and {raw_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m prpilot_data")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gharchive-estimate")
    s = sub.add_parser("gharchive-sample")
    s.add_argument("--budget-gib", type=float, required=True)
    args = parser.parse_args()
    if args.cmd == "gharchive-estimate":
        cmd_estimate()
    else:
        cmd_sample(args.budget_gib)


if __name__ == "__main__":
    main()
