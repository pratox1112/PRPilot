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

uv run python -m prpilot_data diffs-validate [--sample 200]
    Stage C check: GraphQL open-time heads and first-parent bases vs GH Archive on the
    post-trim PRs (where GH Archive has the true head and base).

uv run --group data python -m prpilot_data diffs [--repo owner/name] [--limit N]
    Stage C: open-time diff per PR (REST compare, read-only, cached, resumable), cut to
    6,000 Qwen3-1.7B tokens. With --limit it fetches only the first N PRs per repo (trial).

uv run python -m prpilot_data truth
    Stage D: ground truth per indexed PR (human-applied labels, type, reviewers without bot or
    CODEOWNERS requests, closing issues). Reads stage B; fetches only the repos of closing
    issues (read-only, cached).

uv run --group data python -m prpilot_data dataset
    Stage E: joins stages B-D and the Phase 0 revert scan into one row per PR, splits by
    opening date, drops cross-split duplicates and writes data/processed/*.parquet with hashes.
    No network.

uv run python -m prpilot_data issues [--repo owner/name] [--pages N]
    Every issue (any state, with created/closed times) and every label (with creation time)
    per repo, for the open-time candidate lists. Read-only, cached. --pages: trial run.

uv run --group data python -m prpilot_data candidates
    Open-time candidate lists per PR (labels, top-20 reviewers, top-20 issues) from the
    dataset, issues and labels. No network. Writes data/processed/candidates.jsonl.

uv run --group data python -m prpilot_data prompts
    Renders every PR's exact model input (shared system prompt + user prompt) into
    data/processed/prompts_{split}.jsonl, with hashes and prompt token counts. No network.

uv run --group data python -m prpilot_data targets
    Writes data/processed/targets_{split}.jsonl: the maintainer answers per PR in the shape
    the eval harness scores, with hashes. No network.

uv run --group data python -m prpilot_data rules-baseline [--split val]
    Baseline 1: keyword, path and history rules, written as replies
    (data/processed/replies/rules_<split>.jsonl) for the eval harness. No network.

uv run python -m prpilot_data sft-data --split train --teacher teacher_train [--fill]
    Fine-tuning examples (data/processed/sft_<split>.jsonl): teacher replies that agree with the
    maintainer type, with labels, reviewers and issues overwritten by the maintainer truth
    (prpilot_data/sft.py). Reads every data/processed/replies/<teacher>_*.jsonl (shards).
    --fill: the sft-v2-fill variant (truth first, topped up with the teacher's picks), written
    to sft_<split>_fill.jsonl and results/data_sft_<split>_fill.json.

uv run python -m prpilot_data prompt-sample [--split val] [--n 500] [--seed 0]
    Seeded sample of a prompt pack (data/processed/prompts_<split>_s<n>.jsonl), lines copied
    byte for byte, for the paid Gemini baseline. Other runs are scored on it with
    `prpilot_eval score --only`.

uv run --group data python -m prpilot_data spot-check [--n 40] [--seed 0]
    Writes data/interim/spot_check.md: random train/val PRs with their targets and the actor of
    every label, for the owner's check of the bot and CODEOWNERS filters (§4.6).

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
from prpilot_data.records import (
    REPO_ROOT,
    content_hash,
    read_jsonl,
    require_clean_tree,
    run_metadata,
    write_result,
)

REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
TYPES_CONFIG = REPO_ROOT / "data" / "config" / "type_mappings.toml"
FROZEN_CONFIG = REPO_ROOT / "data" / "config" / "frozen_test.toml"
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

    require_clean_tree()
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


def timeline_nodes(repo: str) -> list[dict]:
    return read_jsonl(
        REPO_ROOT / "data" / "interim" / "timeline" / f"{repo.replace('/', '__')}.jsonl"
    )


def base_index(gh, diffs, repo: str, nodes: list[dict]):
    branch = diffs.base_branch(nodes)
    chain = diffs.first_parent_chain(diffs.fetch_history(gh, repo, branch))
    print(f"{repo}: {len(chain)} first-parent commits on {branch} since {diffs.HISTORY_SINCE}")
    return diffs.BaseIndex(chain)


def cmd_diffs_validate(sample: int) -> None:
    from prpilot_data import diffs
    from prpilot_data.github_api import GitHubGraphQL

    require_clean_tree()
    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", diffs.QUERY_VERSION)
    archive = diffs.load_archive(RAW_DIR / "post_trim_heads.jsonl")
    nodes, indexes = {}, {}
    for r in selected_repos():
        repo_nodes = timeline_nodes(r)
        nodes.update({(r, n["number"]): n for n in repo_nodes})
        indexes[r] = base_index(gh, diffs, r, repo_nodes)
    report = diffs.validate(gh, nodes, archive, indexes, sample)
    path = write_result(
        "data_diffs_validation",
        {
            "stage": "C check: open-time head and base reconstruction vs GH Archive (post-trim)",
            "meta": run_metadata(["httpx"]),
            "query_version": diffs.QUERY_VERSION,
            "sample_seed": 0,
            **report,
            "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        },
    )
    print(f"heads: {report['head_agree']}/{report['post_trim_prs']} agree with GH Archive")
    print(f"   by method {report['head_reconstruction']}")
    print(f"bases ({report['base_sample']} sampled): {report['base_outcomes']}")
    print(f"   diff agreement {report['base_diff_agree']}")
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}; wrote {path}")


def cmd_diffs(repo: str | None, limit: int | None) -> None:
    from prpilot_data import diffs, tokens
    from prpilot_data.github_api import GitHubGraphQL

    if not limit:
        require_clean_tree()
    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", diffs.QUERY_VERSION)
    count_tokens = tokens.load_counter()
    archive = diffs.load_archive(RAW_DIR / "post_trim_heads.jsonl")
    out_dir = REPO_ROOT / "data" / "interim" / "diffs"
    stats = {}
    for r in [repo] if repo else selected_repos():
        nodes = timeline_nodes(r)
        index = base_index(gh, diffs, r, nodes)
        if limit:
            nodes = sorted(nodes, key=lambda n: n["number"])[:limit]
        slug = r.replace("/", "__")
        out = out_dir / (f"{slug}.trial.jsonl" if limit else f"{slug}.jsonl")
        if limit:
            out.unlink(missing_ok=True)  # trials start fresh; only the full run resumes
        written = diffs.process_repo(gh, nodes, archive, index, count_tokens, out)
        stats[r] = diffs.summarize(read_jsonl(out))
        print(f"{r}: {written} new rows; {stats[r]}")
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}")
    if not limit and not repo:
        path = write_result(
            "data_diffs",
            {
                "stage": "C: open-time diffs, truncated to the student token budget",
                "meta": run_metadata(["httpx", "tokenizers", "huggingface-hub"]),
                "query_version": diffs.QUERY_VERSION,
                "tokenizer": {"model": tokens.STUDENT_MODEL, "revision": tokens.STUDENT_REVISION},
                "diff_token_budget": diffs.DIFF_TOKENS,
                "repos": stats,
                "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
            },
        )
        print(f"wrote {path} and {out_dir}")


def cmd_truth() -> None:
    from prpilot_data import ground_truth
    from prpilot_data.github_api import GitHubGraphQL

    require_clean_tree()
    meta = run_metadata(["httpx"])  # taken before the run, so later edits cannot taint it
    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(
        REPO_ROOT / "data" / "raw" / "github_graphql", ground_truth.LINKS_QUERY_VERSION
    )
    repos_cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))
    types_cfg = tomllib.loads(TYPES_CONFIG.read_text(encoding="utf-8"))
    priority = types_cfg["primary_type_priority"]
    out_dir = REPO_ROOT / "data" / "interim" / "truth"
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for r in selected_repos():
        slug = r.replace("/", "__")
        keep = {row["number"] for row in read_jsonl(INDEX_DIR / f"{slug}.jsonl")}
        nodes = [n for n in timeline_nodes(r) if n["number"] in keep]
        with_links = [n["number"] for n in nodes if n["closingIssuesReferences"]["totalCount"]]
        links = ground_truth.fetch_issue_links(gh, r, with_links)
        # Ready-for-review times only matter where the author is the actor of a request.
        with_author_requests = [
            n["number"]
            for n in nodes
            if any(
                (e.get("actor") or {}).get("login") == (n.get("author") or {}).get("login")
                for e in n["reviewRequests"]["nodes"]
            )
        ]
        ready = ground_truth.fetch_ready_events(gh, r, with_author_requests)
        bots = ground_truth.repo_bots(repos_cfg, r)
        mapping = types_cfg["repos"][r]
        rows = [
            ground_truth.truth_row(
                n, bots, mapping, priority, links.get(n["number"], []), ready.get(n["number"], [])
            )
            for n in sorted(nodes, key=lambda n: n["number"])
        ]
        with (out_dir / f"{slug}.jsonl").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        stats[r] = ground_truth.summarize(rows)
        print(f"{r}: {stats[r]}")
    path = write_result(
        "data_truth",
        {
            "stage": "D: ground truth from maintainer actions (labels, type, reviewers, issues)",
            "meta": meta,
            "type_mappings_hash": content_hash(types_cfg),
            "repos_config_hash": content_hash(repos_cfg),
            "codeowners_window_s": ground_truth.CODEOWNERS_WINDOW.total_seconds(),
            "repos": stats,
            "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        },
    )
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}; wrote {path}")


def cmd_dataset() -> None:
    from prpilot_data import dataset

    require_clean_tree()
    meta = run_metadata(["pyarrow"])
    repos_cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))["repos"]
    reverts_path = REPO_ROOT / "results" / "feasibility_reverts.json"
    reverts = dataset.load_reverts(reverts_path)
    interim = REPO_ROOT / "data" / "interim"
    sources = {}
    rows = []
    for r in selected_repos():
        slug = r.replace("/", "__")
        paths = {
            "index": INDEX_DIR / f"{slug}.jsonl",
            "timeline": interim / "timeline" / f"{slug}.jsonl",
            "diffs": interim / "diffs" / f"{slug}.jsonl",
            "truth": interim / "truth" / f"{slug}.jsonl",
        }
        sources[r] = {k: dataset.file_sha256(p) for k, p in paths.items()}
        keep = {row["number"] for row in read_jsonl(paths["index"])}
        nodes = {n["number"]: n for n in read_jsonl(paths["timeline"]) if n["number"] in keep}
        diffs = {d["number"]: d for d in read_jsonl(paths["diffs"]) if d["number"] in keep}
        truth = {t["number"]: t for t in read_jsonl(paths["truth"])}
        missing = keep - nodes.keys() | keep - diffs.keys() | keep - truth.keys()
        if missing:
            raise SystemExit(f"{r}: {len(missing)} indexed PRs lack stage B/C/D rows")
        role = repos_cfg[r]["split_role"]
        for n in sorted(keep):
            node = nodes[n]
            rows.append(
                dataset.build_row(
                    node,
                    diffs[n],
                    truth[n],
                    reverts.get((r, n)),
                    dataset.split_of(node["createdAt"], role),
                )
            )
        print(f"{r}: {len(keep)} PRs")
    kept, duplicates = dataset.drop_cross_split_duplicates(rows)
    dataset.check_no_leakage(kept)
    dataset.check_frozen_test(
        FROZEN_CONFIG, content_hash([row for row in kept if row["split"] == "test"])
    )
    out_dir = REPO_ROOT / "data" / "processed"
    splits = {}
    for split in dataset.SPLITS:
        part = [row for row in kept if row["split"] == split]
        path = out_dir / f"prpilot_{split}.parquet"
        splits[split] = {
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "file_sha256": dataset.write_parquet(part, path),
            "rows_hash": content_hash(part),
            **dataset.summarize(part),
        }
        s = splits[split]
        print(
            f"{split}: {s['prs']} PRs {s['by_repo']}; flagged {s['flagged']} "
            f"({s['flagged_share']}); with type {s['with_type']}, reviewer {s['with_reviewer']}, "
            f"issue {s['with_issue']}, reverted {s['reverted']}"
        )
    path = write_result(
        "data_dataset",
        {
            "stage": "E: cleaned dataset, one row per PR, split by opening date",
            "meta": meta,
            "split_rule": {
                "train": f"opened <= {dataset.TRAIN_END}",
                "val": f"opened <= {dataset.VAL_END}",
                "test": f"opened <= {dataset.TEST_END}, plus every PR of held-out repos",
                "held_out": sorted(
                    k for k in selected_repos() if repos_cfg[k]["split_role"] == "held_out"
                ),
            },
            "inflated_rule": (
                f"open-time files > {dataset.FILES_FACTOR} x final + {dataset.FILES_EXTRA}"
            ),
            "sources": sources,
            "reverts_source": {
                "file": "results/feasibility_reverts.json",
                "sha256": dataset.file_sha256(reverts_path),
            },
            "duplicates": duplicates,
            "splits": splits,
        },
    )
    print(
        f"duplicates across splits: {duplicates['cross_split_groups']} groups, "
        f"dropped {duplicates['dropped_by_split']}"
    )
    print(f"wrote {path} and {out_dir}")


def cmd_issues(repo: str | None, pages: int | None) -> None:
    from prpilot_data import issues
    from prpilot_data.github_api import GitHubGraphQL

    if not pages:
        require_clean_tree()
    meta = run_metadata(["httpx"])
    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(REPO_ROOT / "data" / "raw" / "github_graphql", issues.QUERY_VERSION)
    out_dir = REPO_ROOT / "data" / "interim" / "issues"
    stats = {}
    for r in [repo] if repo else selected_repos():
        slug = r.replace("/", "__")
        out = out_dir / (f"{slug}.trial.jsonl" if pages else f"{slug}.jsonl")
        written, total = issues.fetch_issues(gh, r, out, pages)
        labels = issues.fetch_labels(gh, r)
        (out_dir / f"{slug}.labels.json").write_text(
            json.dumps(labels, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        fetched = {row["number"] for row in read_jsonl(out)}
        # Coverage: closing issues of our PRs (same repo) that the fetch returned.
        truth = read_jsonl(REPO_ROOT / "data" / "interim" / "truth" / f"{slug}.jsonl")
        targets = {i["number"] for t in truth for i in t["issues"] if i["same_repo"]}
        stats[r] = {
            "issues_written": written,
            "issues_total_reported": total,
            "labels": len(labels),
            "closing_issues_found": len(targets & fetched),
            "closing_issues": len(targets),
        }
        print(f"{r}: {stats[r]}")
    print(f"github calls: network {gh.calls}, cache hits {gh.cache_hits}")
    if not pages and not repo:
        path = write_result(
            "data_issues",
            {
                "stage": "Issues and labels per repo, for open-time candidate lists",
                "meta": meta,
                "query_version": issues.QUERY_VERSION,
                "body_chars": issues.BODY_CHARS,
                "repos": stats,
                "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
            },
        )
        print(f"wrote {path} and {out_dir}")


def load_splits(columns: list[str] | None = None) -> list[dict]:
    import pyarrow.parquet as pq

    from prpilot_data import dataset

    processed = REPO_ROOT / "data" / "processed"
    rows = []
    for split in dataset.SPLITS:
        rows += pq.read_table(processed / f"prpilot_{split}.parquet", columns=columns).to_pylist()
    return rows


def cmd_candidates() -> None:
    from prpilot_data import candidates, dataset

    require_clean_tree()
    meta = run_metadata(["numpy", "pyarrow"])
    rows = load_splits()
    issues_dir = REPO_ROOT / "data" / "interim" / "issues"
    uses = candidates.label_vocabulary(rows)
    reviewers = candidates.rank_reviewers(rows)
    print(f"ranked reviewers for {len(reviewers)} PRs")
    cands = {}
    vocab_sizes = {}
    for r in selected_repos():
        slug = r.replace("/", "__")
        repo_labels = json.loads((issues_dir / f"{slug}.labels.json").read_text(encoding="utf-8"))
        index = candidates.IssueIndex(read_jsonl(issues_dir / f"{slug}.jsonl"))
        repo_rows = [row for row in rows if row["repo"] == r]
        sizes = []
        for row in repo_rows:
            labels = candidates.labels_at(row["created_at"], repo_labels, uses[r])
            sizes.append(len(labels))
            key = (r, row["number"])
            cands[key] = candidates.candidate_row(row, labels, reviewers.get(key, []), index)
        vocab_sizes[r] = {"min": min(sizes), "max": max(sizes)}
        print(f"{r}: {len(repo_rows)} PRs, labels per PR {vocab_sizes[r]}")
    out = REPO_ROOT / "data" / "processed" / "candidates.jsonl"
    ordered = [cands[(row["repo"], row["number"])] for row in rows]
    with out.open("w", encoding="utf-8", newline="") as f:
        for c in ordered:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    splits = {}
    for split in dataset.SPLITS:
        part = [row for row in rows if row["split"] == split]
        splits[split] = {
            "prs": len(part),
            "rows_hash": content_hash([cands[(p["repo"], p["number"])] for p in part]),
        }
        # The rankings are the rules baseline, so on test they are a test result: not
        # computed until the test split is run once (hard rule 4).
        if split != "test":
            splits[split].update(candidates.ceilings(part, cands))
        print(f"{split}: {splits[split]}")
    path = write_result(
        "data_candidates",
        {
            "stage": "Open-time candidate lists: labels, reviewers, issues",
            "meta": meta,
            "rules": {
                "label_min_uses": candidates.LABEL_MIN_USES,
                "vocab_before": candidates.VOCAB_BEFORE,
                "top_reviewers": candidates.TOP_REVIEWERS,
                "top_issues": candidates.TOP_ISSUES,
                "path_depth": candidates.PATH_DEPTH,
                "max_files": candidates.MAX_FILES,
                "bm25": {"k1": candidates.BM25_K1, "b": candidates.BM25_B},
            },
            "labels_per_pr": vocab_sizes,
            "splits": splits,
        },
    )
    print(f"wrote {path} and {out}")


def cmd_prompts() -> None:
    import statistics

    from prpilot_data import dataset, prompts, tokens

    require_clean_tree()
    meta = run_metadata(["tokenizers", "pyarrow"])
    rows = load_splits()
    cands = {
        (c["repo"], c["number"]): c
        for c in read_jsonl(REPO_ROOT / "data" / "processed" / "candidates.jsonl")
    }
    count = tokens.load_counter()
    out_dir = REPO_ROOT / "data" / "processed"
    splits = {}
    for split in dataset.SPLITS:
        part = [r for r in rows if r["split"] == split]
        path = out_dir / f"prompts_{split}.jsonl"
        lengths = []
        with path.open("w", encoding="utf-8", newline="") as f:
            for r in part:
                p = prompts.pack_row(r, cands[(r["repo"], r["number"])])
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
                lengths.append(sum(count(m["content"]) for m in p["messages"]))
        lengths.sort()
        splits[split] = {
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "file_sha256": dataset.file_sha256(path),
            "prs": len(part),
            "prompt_tokens": {
                "median": statistics.median(lengths),
                "p95": lengths[int(0.95 * (len(lengths) - 1))],
                "max": lengths[-1],
            },
        }
        print(f"{split}: {splits[split]}")
    path = write_result(
        "data_prompts",
        {
            "stage": "Prompt packs: exact model inputs per PR",
            "meta": meta,
            "pack_version": prompts.PACK_VERSION,
            "tokenizer": {"model": tokens.STUDENT_MODEL, "revision": tokens.STUDENT_REVISION},
            "token_count_note": "system + user message content, without chat-template tokens",
            "splits": splits,
        },
    )
    print(f"wrote {path} and {out_dir}")


def cmd_sft_data(split: str, teacher: str, fill: bool) -> None:
    from collections import Counter

    from prpilot_data import dataset, sft

    require_clean_tree()
    meta = run_metadata([])
    processed = REPO_ROOT / "data" / "processed"
    pack_path = processed / f"prompts_{split}.jsonl"
    pack_sha = dataset.file_sha256(pack_path)
    pack = read_jsonl(pack_path)
    targets = {
        (t["repo"], t["number"]): t for t in read_jsonl(processed / f"targets_{split}.jsonl")
    }

    # Teacher replies may come in shards (`--shard I/N`); together they must cover the pack
    # exactly once, all from this pack.
    files = sorted((processed / "replies").glob(f"{teacher}_*.jsonl"))
    if not files:
        raise SystemExit(f"no replies matching {teacher}_*.jsonl")
    replies, sources = {}, []
    for path in files:
        runner = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
        if runner["pack_sha256"] != pack_sha or runner.get("trial_limit"):
            raise SystemExit(f"{path.name}: made from another pack or a trial run")
        for r in read_jsonl(path):
            key = (r["repo"], r["number"])
            if key in replies:
                raise SystemExit(f"{path.name}: {key} answered twice")
            replies[key] = r["text"]
        sources.append(
            {
                "file": path.name,
                "sha256": dataset.file_sha256(path),
                "shard": runner.get("shard"),
                "slurm_job_id": runner.get("slurm_job_id"),
                "code_commit": runner.get("code_commit"),
                "model": runner["model"],
            }
        )
    missing = len(pack) - sum((r["repo"], r["number"]) in replies for r in pack)

    tag = "_fill" if fill else ""
    out = processed / f"sft_{split}{tag}.jsonl"
    reasons, types, repos, replaced, empty = Counter(), Counter(), Counter(), 0, Counter()
    with out.open("w", encoding="utf-8", newline="") as f:
        for row in pack:
            key = (row["repo"], row["number"])
            ex, reason = sft.example(row, targets[key], replies.get(key), fill)
            reasons[reason] += 1
            if ex is None:
                continue
            types[ex["type"]] += 1
            repos[ex["repo"]] += 1
            replaced += ex["rationale_mentions_replaced"]
            answer = json.loads(ex["messages"][-1]["content"])
            empty["reviewers"] += not answer["reviewers"]
            empty["related_issues"] += not answer["related_issues"]
            f.write(json.dumps({k: ex[k] for k in ("repo", "number", "messages")}) + "\n")
    typed = len(pack) - reasons["no_type"]
    kept = reasons["kept"]
    path = write_result(
        f"data_sft_{split}{tag}",
        {
            "stage": "Fine-tuning examples: agreement-filtered teacher replies, maintainer truth",
            "meta": meta,
            "sft_version": sft.SFT_VERSION_FILL if fill else sft.SFT_VERSION,
            "pack": {"file": pack_path.name, "sha256": pack_sha, "prs": len(pack)},
            "teacher_replies": sources,
            "prs_without_reply": missing,
            "outcome": dict(sorted(reasons.items())),
            "agreement_on_valid_typed": {
                "kept": kept,
                "n": kept + reasons["disagree"],
                "rate": round(kept / max(kept + reasons["disagree"], 1), 4),
            },
            "kept_of_typed": {"kept": kept, "n": typed, "rate": round(kept / max(typed, 1), 4)},
            "per_type": dict(sorted(types.items())),
            "per_repo": dict(sorted(repos.items())),
            "rationale_mentions_replaced": {"count": replaced, "of": kept},
            "empty_answers": {
                "reviewers": empty["reviewers"],
                "related_issues": empty["related_issues"],
                "of": kept,
            },
            "file": {
                "path": str(out.relative_to(REPO_ROOT)).replace("\\", "/"),
                "sha256": dataset.file_sha256(out),
            },
        },
    )
    print(f"kept {kept} of {typed} typed PRs ({dict(reasons)}); wrote {out} and {path}")


def cmd_targets() -> None:
    from prpilot_data import dataset, targets

    require_clean_tree()
    meta = run_metadata(["pyarrow"])
    rows = load_splits()
    cands = {
        (c["repo"], c["number"]): c
        for c in read_jsonl(REPO_ROOT / "data" / "processed" / "candidates.jsonl")
    }
    out_dir = REPO_ROOT / "data" / "processed"
    splits = {}
    for split in dataset.SPLITS:
        path = out_dir / f"targets_{split}.jsonl"
        with path.open("w", encoding="utf-8", newline="") as f:
            for r in rows:
                if r["split"] == split:
                    t = targets.target_row(r, cands[(r["repo"], r["number"])]["labels"])
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
        splits[split] = {
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "file_sha256": dataset.file_sha256(path),
        }
        print(f"{split}: {splits[split]}")
    path = write_result(
        "data_targets",
        {"stage": "Targets files for the eval harness", "meta": meta, "splits": splits},
    )
    print(f"wrote {path}")


def cmd_rules_baseline(split: str) -> None:
    from prpilot_data import dataset, rules_baseline

    require_clean_tree()
    meta = run_metadata(["pyarrow"])
    rows = load_splits()
    processed = REPO_ROOT / "data" / "processed"
    cands = {(c["repo"], c["number"]): c for c in read_jsonl(processed / "candidates.jsonl")}
    hist = rules_baseline.history_predictions(rows)
    out_dir = processed / "replies"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rules_{split}.jsonl"
    with out.open("w", encoding="utf-8", newline="") as f:
        for r in rows:
            if r["split"] == split:
                key = (r["repo"], r["number"])
                line = rules_baseline.reply_line(r, cands[key], hist[key])
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    runner = {
        "name": "rules",
        "model": None,
        "split": split,
        "pack_sha256": dataset.file_sha256(processed / f"prompts_{split}.jsonl"),
        "meta": meta,
        "settings": {
            "label_rate": rules_baseline.LABEL_RATE,
            "label_support": rules_baseline.LABEL_SUPPORT,
            "many_files": rules_baseline.MANY_FILES,
        },
    }
    out.with_name(f"rules_{split}.meta.json").write_text(
        json.dumps(runner, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {out}")


def cmd_prompt_sample(split: str, n: int, seed: int) -> None:
    import random
    from collections import Counter

    from prpilot_data import dataset

    require_clean_tree()
    meta = run_metadata([])
    processed = REPO_ROOT / "data" / "processed"
    parent = processed / f"prompts_{split}.jsonl"
    lines = parent.read_bytes().decode("utf-8").splitlines(keepends=True)
    keep = sorted(random.Random(seed).sample(range(len(lines)), n))
    out = processed / f"prompts_{split}_s{n}.jsonl"
    # Lines are copied byte for byte, so every sampled prompt is identical to the full pack's.
    out.write_bytes("".join(lines[i] for i in keep).encode("utf-8"))
    repos = Counter(json.loads(lines[i])["repo"] for i in keep)
    path = write_result(
        f"data_prompts_{split}_s{n}",
        {
            "stage": "Seeded sample of a prompt pack (paid-API baseline)",
            "meta": meta,
            "parent": {"file": parent.name, "sha256": dataset.file_sha256(parent)},
            "sample": {
                "file": str(out.relative_to(REPO_ROOT)).replace("\\", "/"),
                "sha256": dataset.file_sha256(out),
                "prs": n,
                "seed": seed,
                "per_repo": dict(sorted(repos.items())),
            },
        },
    )
    print(f"wrote {out} and {path}")


def cmd_spot_check(n: int, seed: int) -> None:
    import pyarrow.parquet as pq

    from prpilot_data import spot_check

    processed = REPO_ROOT / "data" / "processed"
    rows = []
    for split in ("train", "val"):
        rows += pq.read_table(processed / f"prpilot_{split}.parquet").to_pylist()
    picked = spot_check.sample(rows, n, seed)
    out = REPO_ROOT / "data" / "interim" / "spot_check.md"
    out.write_text(spot_check.report(picked, seed), encoding="utf-8")
    print(f"wrote {len(picked)} PRs to {out}")


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

    if not month:
        require_clean_tree()
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
            f"{r}: {stats['kept']} PRs kept of {stats['fetched_unique']} found "
            f"(search reported {stats['search_reported']}; other branches dropped "
            f"{stats['off_branch_dropped'] or '-'}), {stats['windows']} windows, "
            f"overfull days {stats['overfull_days'] or '-'}"
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
                "total_prs": sum(s["kept"] for s in all_stats.values()),
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
    dv = sub.add_parser("diffs-validate")
    dv.add_argument("--sample", type=int, default=200)
    df = sub.add_parser("diffs")
    df.add_argument("--repo")
    df.add_argument("--limit", type=int, help="trial run: only the first N PRs per repo")
    sub.add_parser("truth")
    sub.add_parser("dataset")
    iss = sub.add_parser("issues")
    iss.add_argument("--repo")
    iss.add_argument("--pages", type=int, help="trial run: only the first N pages per repo")
    sub.add_parser("candidates")
    sub.add_parser("prompts")
    sub.add_parser("targets")
    rb = sub.add_parser("rules-baseline")
    # No test: the test split is run once, at the end, for every system together.
    rb.add_argument("--split", choices=["train", "val"], default="val")
    sd = sub.add_parser("sft-data")
    # No test: training data never comes from the test split.
    sd.add_argument("--split", choices=["train", "val"], default="train")
    sd.add_argument("--teacher", required=True, help="reply file prefix, e.g. teacher_train")
    sd.add_argument("--fill", action="store_true", help="sft-v2-fill: top up with teacher picks")
    ps = sub.add_parser("prompt-sample")
    # No test: the test split is run once, at the end, for every system together.
    ps.add_argument("--split", choices=["train", "val"], default="val")
    ps.add_argument("--n", type=int, default=500)
    ps.add_argument("--seed", type=int, default=0)
    sc = sub.add_parser("spot-check")
    sc.add_argument("--n", type=int, default=40)
    sc.add_argument("--seed", type=int, default=0)
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
    elif args.cmd == "diffs-validate":
        cmd_diffs_validate(args.sample)
    elif args.cmd == "diffs":
        cmd_diffs(args.repo, args.limit)
    elif args.cmd == "truth":
        cmd_truth()
    elif args.cmd == "dataset":
        cmd_dataset()
    elif args.cmd == "issues":
        cmd_issues(args.repo, args.pages)
    elif args.cmd == "candidates":
        cmd_candidates()
    elif args.cmd == "prompts":
        cmd_prompts()
    elif args.cmd == "targets":
        cmd_targets()
    elif args.cmd == "rules-baseline":
        cmd_rules_baseline(args.split)
    elif args.cmd == "sft-data":
        cmd_sft_data(args.split, args.teacher, args.fill)
    elif args.cmd == "prompt-sample":
        cmd_prompt_sample(args.split, args.n, args.seed)
    elif args.cmd == "spot-check":
        cmd_spot_check(args.n, args.seed)
    else:
        cmd_validate()


if __name__ == "__main__":
    main()
