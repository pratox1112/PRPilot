"""Task 0.6, step 1: build the 200-PR sample for the teacher agreement check.

Pool: merged PRs from the train window (2023-01-01..2024-12-31) of the time-split repos in
data/config/repos.toml (the held-out repo is test-only and never labelled by the teacher),
sampled with the same queries as task 0.4 (up to 100 most recently created per year), that
have a human-applied type label. Target: the primary type from type_mappings.toml.
200 PRs are drawn at random (fixed seed) and their inputs fetched read-only from GitHub.

Feasibility approximations, recorded in the output (Phase 1 does these properly):
- final title, body and diff, not the versions at PR open time;
- slash-command lines are stripped from the body (they apply the label in some repos);
- diff truncated to a character budget, outline of all files and hunks kept first.

Usage:
    uv run python scripts/feasibility/agreement_sample.py
Writes data/processed/teacher_agreement_inputs.jsonl (gitignored; copied to Explorer) and
results/feasibility_teacher_sample.json.
"""

import hashlib
import json
import random
import tomllib
from collections import Counter

import httpx
from common import REPO_ROOT, content_hash, run_metadata, write_result
from dotenv import load_dotenv
from label_volume import (
    CACHE_DIR,
    PAGE_SIZE,
    PR_QUERY,
    QUERY_VERSION,
    REPO_QUERY,
    as_timeline,
    load_bots,
    search_q,
)

from prpilot_data.github_api import GitHubGraphQL
from prpilot_data.inputs import changed_files, strip_label_commands, truncate_diff
from prpilot_data.labels import attribute_labels, is_human_applied

REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
MAPPINGS_CONFIG = REPO_ROOT / "data" / "config" / "type_mappings.toml"
OUT_PATH = REPO_ROOT / "data" / "processed" / "teacher_agreement_inputs.jsonl"
TRAIN_WINDOWS = [("2023", "2023-01-01", "2023-12-31"), ("2024", "2024-01-01", "2024-12-31")]
PER_WINDOW = 100
SAMPLE_SIZE = 200
SEED = 0
DIFF_CHARS = 24_000
MAX_FILES = 100


def primary_type(types: set[str], priority: list[str]) -> str | None:
    return next((t for t in priority if t in types), None)


def window_prs(gh: GitHubGraphQL, repo: str, start: str, end: str) -> list[dict]:
    q = search_q(repo, start, end)
    prs, after = [], None
    while len(prs) < PER_WINDOW:
        first = min(PAGE_SIZE, PER_WINDOW - len(prs))
        s = gh.query(PR_QUERY, {"q": q, "first": first, "after": after})["search"]
        prs += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            break
        after = s["pageInfo"]["endCursor"]
    return prs


def repo_labels(gh: GitHubGraphQL, repo: str) -> list[str]:
    owner, name = repo.split("/")
    labels, after = [], None
    while True:
        r = gh.query(REPO_QUERY, {"owner": owner, "name": name, "after": after})["repository"]
        labels += [lab["name"] for lab in r["labels"]["nodes"]]
        if not r["labels"]["pageInfo"]["hasNextPage"]:
            return labels
        after = r["labels"]["pageInfo"]["endCursor"]


def fetch_inputs(gh: GitHubGraphQL, repo: str, number: int) -> dict:
    pr = json.loads(gh.rest_get(f"/repos/{repo}/pulls/{number}"))
    files = json.loads(gh.rest_get(f"/repos/{repo}/pulls/{number}/files?per_page={MAX_FILES}"))
    try:
        diff = gh.rest_get(f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff")
        diff_status = "ok"
    except httpx.HTTPStatusError as e:  # GitHub refuses very large diffs
        diff, diff_status = "", f"unavailable (HTTP {e.response.status_code})"
    paths = tuple(f["filename"] for f in files) or changed_files(diff)
    body = pr.get("body") or ""
    clean_body = strip_label_commands(body)
    truncated, was_cut = truncate_diff(diff, DIFF_CHARS)
    return {
        "title": pr["title"],
        "body": clean_body,
        "author": (pr.get("user") or {}).get("login") or "",
        "base_branch": pr["base"]["ref"],
        "changed_files": list(paths),
        "diff": truncated,
        "flags": {
            "body_commands_stripped": clean_body != body.strip(),
            "diff_truncated": was_cut,
            "diff_status": diff_status,
            "files_capped": len(files) >= MAX_FILES,
        },
        "created_at": pr["created_at"],
        "merged_at": pr["merged_at"],
    }


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    repos_cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))["repos"]
    repos = [r for r, c in repos_cfg.items() if c.get("split_role") == "train_val_test"]
    mcfg = tomllib.loads(MAPPINGS_CONFIG.read_text(encoding="utf-8"))
    priority, mappings = mcfg["primary_type_priority"], mcfg["repos"]
    bots = load_bots()
    gh = GitHubGraphQL(CACHE_DIR, QUERY_VERSION)

    pool = []
    for repo in repos:
        for wname, start, end in TRAIN_WINDOWS:
            prs = window_prs(gh, repo, start, end)
            kept = 0
            for pr in prs:
                attrs = attribute_labels(as_timeline(pr), bots[repo])
                human = sorted(a.label for a in attrs.values() if is_human_applied(a))
                types = {mappings[repo][lab] for lab in human if lab in mappings[repo]}
                ptype = primary_type(types, priority)
                if ptype:
                    pool.append(
                        {
                            "repo": repo,
                            "number": pr["number"],
                            "window": wname,
                            "maintainer_type": ptype,
                            "maintainer_types": sorted(types),
                            "human_labels": human,
                        }
                    )
                    kept += 1
            print(f"  {repo} {wname}: sampled {len(prs)}, with a type label {kept}")

    pool.sort(key=lambda r: (r["repo"], r["number"]))
    if len(pool) < SAMPLE_SIZE:
        raise SystemExit(f"pool has only {len(pool)} PRs; need {SAMPLE_SIZE}")
    chosen = random.Random(SEED).sample(pool, SAMPLE_SIZE)
    chosen.sort(key=lambda r: (r["repo"], r["number"]))

    labels = {repo: repo_labels(gh, repo) for repo in repos}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for i, row in enumerate(chosen, 1):
            row = {
                **row,
                "repo_labels": labels[row["repo"]],
                "context": fetch_inputs(gh, row["repo"], row["number"]),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if i % 25 == 0:
                print(f"  fetched inputs for {i}/{SAMPLE_SIZE}")
    file_hash = "sha256:" + hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()

    rows = [
        json.loads(line)
        for line in OUT_PATH.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    flags = Counter()
    for r in rows:
        for k, v in r["context"]["flags"].items():
            flags[f"{k}={v}"] += 1
    payload = {
        "task": "0.6 (sample)",
        "meta": run_metadata(["httpx"]),
        "inputs_file": str(OUT_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "inputs_hash": file_hash,
        "pool_size": len(pool),
        "pool_hash": content_hash([(r["repo"], r["number"]) for r in pool]),
        "sample_size": SAMPLE_SIZE,
        "seed": SEED,
        "by_repo": dict(Counter(r["repo"] for r in rows)),
        "by_type": dict(Counter(r["maintainer_type"] for r in rows)),
        "multi_type_prs": sum(len(r["maintainer_types"]) > 1 for r in rows),
        "flags": dict(flags),
        "settings": {
            "diff_chars": DIFF_CHARS,
            "max_files": MAX_FILES,
            "train_windows": TRAIN_WINDOWS,
            "per_window": PER_WINDOW,
        },
        "approximations": [
            "final title/body/diff, not open-time versions",
            "slash-command lines stripped from the body",
            "diff truncated to diff_chars with a file/hunk outline first",
            "pool = most recently created merged PRs per year, not uniform",
        ],
        "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
    }
    path = write_result("feasibility_teacher_sample", payload)
    print(f"\nby repo: {payload['by_repo']}\nby type: {payload['by_type']}")
    print(f"wrote {OUT_PATH} ({file_hash[:19]}...) and {path}")


if __name__ == "__main__":
    main()
