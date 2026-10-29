"""Command line for the data pipeline.

uv run --group data python -m prpilot_data gharchive-estimate
    Dry-runs the sample and full GH Archive queries; prints bytes and cost. Free.

uv run --group data python -m prpilot_data gharchive-sample --days sample|validation --budget-gib N
    Runs a day-sample query (only if its estimate is <= N GiB) and reports how many
    `opened` events still carry the open-time fields.

uv run python -m prpilot_data discover [--repo owner/name] [--month YYYY-MM]
    Stage A: lists merged default-branch PRs opened in the data range (read-only GitHub
    search, cached). With --month it only prints counts for that month (a trial run).

uv run python -m prpilot_data timeline [--repo owner/name] [--limit N]
    Stage B: fetches the GraphQL timeline of every discovered PR (read-only, cached,
    resumable). With --limit it fetches only the first N PRs of each repo (a trial run).

uv run --group data python -m prpilot_data gharchive-post-trim --budget-gib N
    Stage A': open-time head SHAs from GH Archive for 2025-10..2026-03 (payloads after GitHub's
    2025-10-07 trim still carry them). Dry-run first; refuses to run above N GiB.

uv run python -m prpilot_data complete
    Stage B follow-up: refetches, page by page, every connection whose single page was full
    (reviews, comments, label events, review requests, renames/force pushes, commits).

uv run python -m prpilot_data open-time-validate
    Rebuilds the open-time state of every sampled PR from GraphQL (read-only, cached) and
    compares it with the GH Archive payloads.
"""

import argparse
import json
import os
import tomllib

from dotenv import load_dotenv

from prpilot_data import gharchive
from prpilot_data.records import REPO_ROOT, content_hash, read_jsonl, run_metadata, write_result

REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
RAW_DIR = REPO_ROOT / "data" / "raw" / "gharchive"
INDEX_DIR = REPO_ROOT / "data" / "interim" / "index"


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
        (
            f"validation ({len(gharchive.VALIDATION_DAYS)} days)",
            gharchive.days_sql(gharchive.VALIDATION_DAYS),
        ),
        ("post-trim only (2025-10..2026-03)", gharchive.post_trim_sql()),
        ("full (2023-01..2026-03)", gharchive.full_sql()),
    ]:
        e = estimate(bigquery, client, params, sql)
        print(
            f"{name}: {e.gib:,.1f} GiB scanned, ~${e.usd():,.2f} at on-demand list price "
            "(first 1 TiB per month is free)"
        )


def cmd_sample(day_set: str, budget_gib: float) -> None:
    bigquery, client, params = client_and_config()
    days = gharchive.DAY_SETS[day_set]
    sql = gharchive.days_sql(days)
    e = estimate(bigquery, client, params, sql)
    cfg = bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=gharchive.byte_cap(e, budget_gib)
    )
    job = client.query(sql, job_config=cfg)
    rows = [dict(r) for r in job.result()]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{day_set}_opened.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    summary = gharchive.completeness(rows)
    path = write_result(
        f"data_gharchive_{day_set}",
        {
            "step": "Phase 1 step 2: GH Archive payload completeness",
            "meta": run_metadata(["google-cloud-bigquery"]),
            "days": days,
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


def cmd_post_trim(budget_gib: float) -> None:
    from collections import Counter

    bigquery, client, params = client_and_config()
    sql = gharchive.post_trim_sql()
    e = estimate(bigquery, client, params, sql)
    cfg = bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=gharchive.byte_cap(e, budget_gib)
    )
    job = client.query(sql, job_config=cfg)
    keep = ("repo", "number", "head_sha", "base_sha", "base_ref", "event_created_at")
    rows = [{k: r[k] for k in keep} for r in job.result()]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Not named *_opened.jsonl: open-time-validate globs those as its truth set.
    raw_path = RAW_DIR / "post_trim_heads.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    by_repo = Counter(r["repo"] for r in rows)
    with_sha = sum(bool(r["head_sha"]) for r in rows)
    path = write_result(
        "data_gharchive_post_trim",
        {
            "stage": "A': GH Archive open-time head SHAs after the 2025-10-07 payload trim",
            "meta": run_metadata(["google-cloud-bigquery"]),
            "months": gharchive.POST_TRIM_MONTHS,
            "bytes_estimated": e.bytes_processed,
            "bytes_billed": job.total_bytes_billed,
            "opened_events": len(rows),
            "with_head_sha": with_sha,
            "by_repo": dict(by_repo),
            "raw_file": str(raw_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        },
    )
    print(f"{len(rows)} opened events ({with_sha} with head SHA): {dict(by_repo)}")
    print(f"billed {job.total_bytes_billed / 2**30:.1f} GiB; wrote {path} and {raw_path}")


def cmd_complete() -> None:
    from collections import Counter

    from prpilot_data import complete
    from prpilot_data.github_api import GitHubGraphQL

    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", complete.QUERY_VERSION)
    stats = {}
    for r in selected_repos():
        path = REPO_ROOT / "data" / "interim" / "timeline" / f"{r.replace('/', '__')}.jsonl"
        rows = read_jsonl(path)
        counts: Counter = Counter()
        for row in rows:
            _, keys = complete.complete_node(gh, row)
            counts.update(keys)
            counts["prs"] += bool(keys)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        stats[r] = dict(counts)
        print(f"{r}: completed {dict(counts)}")
    path = write_result(
        "data_timeline_complete",
        {
            "stage": "B follow-up: complete connections that hit their page size",
            "meta": run_metadata(["httpx"]),
            "repos": stats,
            "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        },
    )
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}; wrote {path}")


def cmd_validate() -> None:
    from prpilot_data.github_api import GitHubGraphQL
    from prpilot_data.validate_open_time import QUERY_VERSION, load_truth, validate

    load_dotenv(REPO_ROOT / ".env")
    paths = sorted(RAW_DIR.glob("*_opened.jsonl"))
    if not paths:
        raise SystemExit(f"no GH Archive samples in {RAW_DIR}; run gharchive-sample first")
    truth = load_truth(paths)
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", QUERY_VERSION)
    report = validate(gh, truth)
    path = write_result(
        "data_open_time_validation",
        {
            "step": "Phase 1 step 2: GraphQL open-time reconstruction vs GH Archive",
            "meta": run_metadata(["httpx"]),
            "truth_files": [str(p.relative_to(REPO_ROOT)).replace("\\", "/") for p in paths],
            "truth_hash": content_hash(sorted(f"{r}#{n}" for r, n in truth)),
            "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
            **report,
        },
    )
    for tol, r in report["by_tolerance"].items():
        print(f"== tolerance {tol}: head methods {r['head_methods']}")
        for period in ("before_trim", "after_trim"):
            s = r[period]
            print(f"   {period:12}", {f: (v["agree"], v["n"]) for f, v in s.items()})
    print(f"open-time head commit still fetchable: {report['true_head_commit_fetchable']}")
    print(f"fetched {report['prs_fetched']}/{report['prs_in_truth']} PRs; wrote {path}")


def cmd_discover(repo: str | None, month: str | None) -> None:
    from datetime import date, timedelta

    from prpilot_data import discover
    from prpilot_data.github_api import GitHubGraphQL

    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", discover.QUERY_VERSION)
    repos = [repo] if repo else selected_repos()
    if month:
        y, m = map(int, month.split("-"))
        start = date(y, m, 1)
        end = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
    else:
        start, end = discover.RANGE_START, discover.RANGE_END
    all_stats = {}
    for r in repos:
        rows, stats = discover.discover_repo(gh, r, start, end)
        all_stats[r] = stats
        print(
            f"{r}: {stats['fetched_unique']} PRs (search reported {stats['search_reported']}), "
            f"{stats['windows']} windows, overfull days {stats['overfull_days'] or '-'}"
        )
        if not month:
            discover.write_index(rows, INDEX_DIR / f"{r.replace('/', '__')}.jsonl")
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}")
    if not month:
        path = write_result(
            "data_discover",
            {
                "stage": "A: discover merged default-branch PRs",
                "meta": run_metadata(["httpx"]),
                "range": [str(start), str(end)],
                "repos": all_stats,
                "total_prs": sum(s["fetched_unique"] for s in all_stats.values()),
                "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
            },
        )
        print(f"wrote {path} and {INDEX_DIR}")


def cmd_timeline(repo: str | None, limit: int | None) -> None:
    from collections import Counter

    from prpilot_data import timeline
    from prpilot_data.github_api import GitHubGraphQL

    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", timeline.QUERY_VERSION)
    repos = [repo] if repo else selected_repos()
    out_dir = REPO_ROOT / "data" / "interim" / "timeline"
    stats = {}
    for r in repos:
        slug = r.replace("/", "__")
        numbers = timeline.read_index(INDEX_DIR / f"{slug}.jsonl")
        if limit:
            numbers = sorted(numbers)[:limit]
        out = out_dir / (f"{slug}.trial.jsonl" if limit else f"{slug}.jsonl")
        written, missing, failed = timeline.fetch_repo(gh, r, numbers, out)
        capped: Counter = Counter()
        for row in read_jsonl(out):
            for k, v in timeline.capped_counts(row).items():
                capped[k] += v
        stats[r] = {
            "requested": len(numbers),
            "written": written,
            "missing": missing,
            "failed": failed,
            "capped": dict(capped),
        }
        print(
            f"{r}: {written}/{len(numbers)} written, {missing} missing, {len(failed)} failed; "
            f"capped {dict(capped)}"
        )
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}")
    if not limit:
        path = write_result(
            "data_timeline",
            {
                "stage": "B: GraphQL timeline per PR",
                "meta": run_metadata(["httpx"]),
                "query_version": timeline.QUERY_VERSION,
                "repos": stats,
                "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
            },
        )
        print(f"wrote {path} and {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m prpilot_data")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gharchive-estimate")
    s = sub.add_parser("gharchive-sample")
    s.add_argument("--days", choices=sorted(gharchive.DAY_SETS), default="sample")
    s.add_argument("--budget-gib", type=float, required=True)
    sub.add_parser("open-time-validate")
    d = sub.add_parser("discover")
    d.add_argument("--repo")
    d.add_argument("--month", help="YYYY-MM: trial run for one month, counts only")
    pt = sub.add_parser("gharchive-post-trim")
    pt.add_argument("--budget-gib", type=float, required=True)
    sub.add_parser("complete")
    t = sub.add_parser("timeline")
    t.add_argument("--repo")
    t.add_argument("--limit", type=int, help="trial run: only the first N PRs per repo")
    args = parser.parse_args()
    if args.cmd == "gharchive-estimate":
        cmd_estimate()
    elif args.cmd == "gharchive-sample":
        cmd_sample(args.days, args.budget_gib)
    elif args.cmd == "discover":
        cmd_discover(args.repo, args.month)
    elif args.cmd == "timeline":
        cmd_timeline(args.repo, args.limit)
    elif args.cmd == "gharchive-post-trim":
        cmd_post_trim(args.budget_gib)
    elif args.cmd == "complete":
        cmd_complete()
    else:
        cmd_validate()


if __name__ == "__main__":
    main()
