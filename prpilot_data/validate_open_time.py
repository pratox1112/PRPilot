"""Validate GraphQL open-time reconstruction against GH Archive `opened` payloads.

Truth: GH Archive `opened` events (title/body only before GitHub trimmed event payloads on
2025-10-07; head SHA throughout). For each sampled PR we fetch the GraphQL history, rebuild
the open-time state with `open_time.reconstruct`, and report per-field agreement, next to a
naive baseline that simply takes today's final values.
"""

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx

from prpilot_data.github_api import GitHubGraphQL
from prpilot_data.open_time import PR_OPEN_TIME_FIELDS, normalize_text, reconstruct

PAYLOAD_TRIM_DATE = "2025-10-07"  # GitHub changelog 2025-08-08: event payloads slimmed
QUERY_VERSION = "open-time-v1"
BATCH = 20
TOLERANCES = {"0s": timedelta(0), "5min": timedelta(minutes=5)}


def load_truth(paths: list[Path]) -> dict[tuple[str, int], dict]:
    truth = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("number") is not None:
                truth.setdefault((r["repo"], int(r["number"])), r)
    return truth


def batch_query(numbers: list[int]) -> str:
    aliases = "\n".join(
        f"  pr{n}: pullRequest(number: {n}) {{{PR_OPEN_TIME_FIELDS}\n  }}" for n in numbers
    )
    return f"""query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
{aliases}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""


def fetch_prs(gh: GitHubGraphQL, keys: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    out = {}
    by_repo: dict[str, list[int]] = {}
    for repo, n in keys:
        by_repo.setdefault(repo, []).append(n)
    for repo, numbers in by_repo.items():
        owner, name = repo.split("/")
        numbers = sorted(numbers)
        for i in range(0, len(numbers), BATCH):
            chunk = numbers[i : i + BATCH]
            data = gh.query(batch_query(chunk), {"owner": owner, "name": name})
            for n in chunk:
                node = data["repository"].get(f"pr{n}")
                if node:
                    out[(repo, n)] = node
    return out


def compare(truth: dict, reconstructed: dict, final: dict) -> dict:
    """Field agreement for one PR. None = no truth available for that field."""
    has_text = truth.get("title") is not None

    def same_text(a, b):
        return normalize_text(a) == normalize_text(b)

    return {
        "title": truth["title"] == reconstructed["title"] if has_text else None,
        "body": same_text(truth.get("body"), reconstructed["body"]) if has_text else None,
        "head_sha": truth["head_sha"] == reconstructed["head_sha"],
        "naive_title": truth["title"] == final["title"] if has_text else None,
        "naive_body": same_text(truth.get("body"), final.get("body")) if has_text else None,
        "naive_head_sha": truth["head_sha"] == final["headRefOid"],
    }


def summarize(rows: list[dict]) -> dict:
    fields = ["title", "body", "head_sha", "naive_title", "naive_body", "naive_head_sha"]
    out = {}
    for f in fields:
        vals = [r[f] for r in rows if r[f] is not None]
        rate = round(sum(vals) / len(vals), 4) if vals else None
        out[f] = {"n": len(vals), "agree": sum(vals), "rate": rate}
    return out


def validate(gh: GitHubGraphQL, truth: dict) -> dict:
    prs = fetch_prs(gh, sorted(truth))
    report = {"prs_in_truth": len(truth), "prs_fetched": len(prs), "by_tolerance": {}}
    for tol_name, tol in TOLERANCES.items():
        rows, methods, mismatches = [], Counter(), []
        for key, node in prs.items():
            t = truth[key]
            rec = reconstruct(node, tol)
            row = compare(t, rec, node)
            row["period"] = (
                "before_trim"
                if str(t["event_created_at"])[:10] < PAYLOAD_TRIM_DATE
                else "after_trim"
            )
            rows.append(row)
            methods[rec["head_method"]] += 1
            bad = [f for f in ("title", "body", "head_sha") if row[f] is False]
            if bad:
                mismatches.append(
                    {
                        "repo": key[0],
                        "number": key[1],
                        "fields": bad,
                        "head_method": rec["head_method"],
                        **rec["flags"],
                    }
                )
        report["by_tolerance"][tol_name] = {
            "all": summarize(rows),
            "before_trim": summarize([r for r in rows if r["period"] == "before_trim"]),
            "after_trim": summarize([r for r in rows if r["period"] == "after_trim"]),
            "head_methods": dict(methods),
            "mismatches": mismatches,
        }
    report["true_head_commit_fetchable"] = commit_availability(gh, truth, prs)
    return report


def commit_exists(gh: GitHubGraphQL, repo: str, sha: str) -> bool:
    """Can the commit still be fetched? (Needed to build an open-time diff from its SHA.)"""
    try:
        gh.rest_get(f"/repos/{repo}/commits/{sha}", accept="application/vnd.github.sha")
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 422):
            return False
        raise


def commit_availability(gh: GitHubGraphQL, truth: dict, prs: dict) -> dict:
    """For each sampled PR: does GH Archive's open-time head commit still exist on GitHub?
    Split by whether the PR was force-pushed, which is where GraphQL cannot recover it."""
    groups: dict[str, Counter] = {"force_pushed": Counter(), "not_force_pushed": Counter()}
    for key, node in prs.items():
        pushed = any(
            n.get("__typename") == "HeadRefForcePushedEvent" for n in node["timelineItems"]["nodes"]
        )
        g = groups["force_pushed" if pushed else "not_force_pushed"]
        g["n"] += 1
        g["exists"] += commit_exists(gh, key[0], truth[key]["head_sha"])
    return {name: dict(c) for name, c in groups.items()}
