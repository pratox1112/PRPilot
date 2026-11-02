"""Task 0.4: how many PRs have a human-applied type label?

Two steps, both reading the same cached GitHub responses:

    uv run python scripts/feasibility/label_volume.py fetch
        Samples merged PRs per candidate repo, attributes every label to a human or a bot, and
        writes a per-repo label report used to write data/config/type_mappings.toml.

    uv run python scripts/feasibility/label_volume.py estimate
        Applies the type mappings and estimates usable PRs over the full date range.
"""

import argparse
import math
import re
import sys
import tomllib
from collections import Counter, defaultdict

from common import REPO_ROOT, content_hash, run_metadata, write_result
from dotenv import load_dotenv

from prpilot_data.github_api import GitHubGraphQL
from prpilot_data.labels import RepoBots, attribute_labels, is_human_applied

QUERY_VERSION = "label-volume-v1"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "github_graphql"
REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
MAPPINGS_CONFIG = REPO_ROOT / "data" / "config" / "type_mappings.toml"
PER_WINDOW = 50
PAGE_SIZE = 25
WINDOWS = [
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2-2026Q1", "2025-07-01", "2026-03-31"),
]
GO_THRESHOLD = 3000
SUSPECT_BOT = re.compile(r"bot|robot|automation|\bci\b|-ci-", re.IGNORECASE)

RATE = "rateLimit { cost remaining resetAt }"
REPO_QUERY = f"""
query($owner: String!, $name: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    nameWithOwner isArchived licenseInfo {{ spdxId }}
    labels(first: 100, after: $after) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ name description }}
    }}
  }}
  {RATE}
}}"""
COUNT_QUERY = f"""
query($q: String!) {{ search(query: $q, type: ISSUE, first: 1) {{ issueCount }} {RATE} }}"""
ACTOR = "actor { __typename login }"
PR_QUERY = f"""
query($q: String!, $first: Int!, $after: String) {{
  search(query: $q, type: ISSUE, first: $first, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      ... on PullRequest {{
        number title createdAt mergedAt body
        author {{ __typename login }}
        labelEvents: timelineItems(first: 100, itemTypes: [LABELED_EVENT, UNLABELED_EVENT]) {{
          totalCount
          nodes {{
            __typename
            ... on LabeledEvent {{ createdAt {ACTOR} label {{ name }} }}
            ... on UnlabeledEvent {{ createdAt {ACTOR} label {{ name }} }}
          }}
        }}
        comments: timelineItems(first: 50, itemTypes: [ISSUE_COMMENT]) {{
          totalCount
          nodes {{
            __typename
            ... on IssueComment {{ createdAt author {{ __typename login }} body }}
          }}
        }}
      }}
    }}
  }}
  {RATE}
}}"""


def load_bots() -> dict[str, RepoBots]:
    cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))
    global_bots = set(cfg.get("global", {}).get("bot_logins", []))
    return {
        repo: RepoBots(
            bot_logins=frozenset(global_bots | set(c.get("bot_logins", []))),
            command_bots=frozenset(c.get("command_bots", [])),
        )
        for repo, c in cfg["repos"].items()
    }


def search_q(repo: str, start: str, end: str) -> str:
    return f"repo:{repo} is:pr is:merged merged:{start}..{end} sort:created-desc"


def fetch_repo(gh: GitHubGraphQL, repo: str) -> dict:
    owner, name = repo.split("/")
    labels, after, meta = [], None, None
    while True:
        r = gh.query(REPO_QUERY, {"owner": owner, "name": name, "after": after})["repository"]
        meta = meta or r
        labels += r["labels"]["nodes"]
        if not r["labels"]["pageInfo"]["hasNextPage"]:
            break
        after = r["labels"]["pageInfo"]["endCursor"]

    windows = {}
    for wname, start, end in WINDOWS:
        q = search_q(repo, start, end)
        count = gh.query(COUNT_QUERY, {"q": q})["search"]["issueCount"]
        prs, after = [], None
        while len(prs) < PER_WINDOW:
            first = min(PAGE_SIZE, PER_WINDOW - len(prs))
            s = gh.query(PR_QUERY, {"q": q, "first": first, "after": after})["search"]
            prs += [n for n in s["nodes"] if n]
            if not s["pageInfo"]["hasNextPage"]:
                break
            after = s["pageInfo"]["endCursor"]
        windows[wname] = {"merged_count": count, "prs": prs}
        print(f"  {repo} {wname}: {count} merged, sampled {len(prs)}")
    return {
        "license": (meta["licenseInfo"] or {}).get("spdxId"),
        "archived": meta["isArchived"],
        "labels": labels,
        "windows": windows,
    }


def as_timeline(pr: dict) -> dict:
    """Merge the two aliased timeline connections into the shape label_attribution expects."""
    return {**pr, "timelineItems": {"nodes": pr["labelEvents"]["nodes"] + pr["comments"]["nodes"]}}


def fetch_all() -> tuple[dict, GitHubGraphQL]:
    load_dotenv(REPO_ROOT / ".env")
    gh = GitHubGraphQL(CACHE_DIR, QUERY_VERSION)
    bots = load_bots()
    data = {}
    for repo in bots:
        print(f"fetching {repo}")
        data[repo] = fetch_repo(gh, repo)
    return data, gh


def sample_hash(data: dict) -> str:
    return content_hash(
        {
            repo: {w: [p["number"] for p in v["prs"]] for w, v in d["windows"].items()}
            for repo, d in data.items()
        }
    )


def label_report(repo: str, d: dict, bots: RepoBots) -> dict:
    by_label: dict[str, Counter] = defaultdict(Counter)
    actors: Counter = Counter()
    actor_types: dict[str, str] = {}
    truncated = {"label_events": 0, "comments": 0}
    n = 0
    any_human = 0
    for w in d["windows"].values():
        for pr in w["prs"]:
            n += 1
            truncated["label_events"] += pr["labelEvents"]["totalCount"] > 100
            truncated["comments"] += pr["comments"]["totalCount"] > 50
            attrs = attribute_labels(as_timeline(pr), bots)
            any_human += any(is_human_applied(a) for a in attrs.values())
            for a in attrs.values():
                by_label[a.label][a.kind] += 1
            for ev in pr["labelEvents"]["nodes"]:
                if ev.get("__typename") == "LabeledEvent" and ev.get("actor"):
                    login = ev["actor"]["login"]
                    actors[login] += 1
                    actor_types[login] = ev["actor"]["__typename"]

    labels = sorted(by_label.items(), key=lambda kv: -sum(kv[1].values()))
    top_actors = [
        {
            "login": login,
            "type": actor_types[login],
            "label_events": count,
            "classified_bot": login.endswith("[bot]")
            or actor_types[login] == "Bot"
            or login in bots.bot_logins,
            "suspect_bot": bool(SUSPECT_BOT.search(login)),
        }
        for login, count in actors.most_common(15)
    ]
    return {
        "license": d["license"],
        "archived": d["archived"],
        "merged_counts": {w: v["merged_count"] for w, v in d["windows"].items()},
        "sampled_prs": n,
        "prs_with_any_human_label": any_human,
        "truncated_timelines": truncated,
        "final_labels": [{"label": k, **dict(v)} for k, v in labels],
        "top_label_actors": top_actors,
        "repo_labels": [
            {"name": lab["name"], "description": lab["description"]} for lab in d["labels"]
        ],
    }


def cmd_fetch() -> None:
    data, gh = fetch_all()
    bots = load_bots()
    repos = {repo: label_report(repo, d, bots[repo]) for repo, d in data.items()}
    payload = {
        "task": "0.4",
        "step": "fetch (label attribution report)",
        "meta": run_metadata(["httpx"]),
        "query_version": QUERY_VERSION,
        "windows": WINDOWS,
        "per_window_sample": PER_WINDOW,
        "sample_hash": sample_hash(data),
        "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        "repos": repos,
    }
    path = write_result("feasibility_label_volume_pass1", payload)
    print("\nrepo | license | merged in range | sampled | with any human label | suspect bots")
    for repo, r in repos.items():
        suspects = [
            a["login"]
            for a in r["top_label_actors"]
            if a["suspect_bot"] and not a["classified_bot"]
        ]
        print(
            f"{repo} | {r['license']} | {sum(r['merged_counts'].values())} | {r['sampled_prs']} | "
            f"{r['prs_with_any_human_label']} | {suspects or '-'}"
        )
    print(f"\nwrote {path}")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(centre - half, 0.0), min(centre + half, 1.0))


def cmd_estimate() -> None:
    if not MAPPINGS_CONFIG.exists():
        sys.exit(f"missing {MAPPINGS_CONFIG}; write the type mappings first")
    mappings = tomllib.loads(MAPPINGS_CONFIG.read_text(encoding="utf-8"))["repos"]
    data, gh = fetch_all()
    bots = load_bots()
    repos, total, total_lo, total_hi = {}, 0.0, 0.0, 0.0
    for repo, d in data.items():
        mapping = mappings.get(repo, {})
        per_window, est, lo, hi = {}, 0.0, 0.0, 0.0
        types: Counter = Counter()
        multi_type = 0
        for wname, w in d["windows"].items():
            n = len(w["prs"])
            k = 0
            for pr in w["prs"]:
                attrs = attribute_labels(as_timeline(pr), bots[repo])
                pr_types = {
                    mapping[a.label]
                    for a in attrs.values()
                    if is_human_applied(a) and a.label in mapping
                }
                if pr_types:
                    k += 1
                    types.update(pr_types)
                    multi_type += len(pr_types) > 1
            share = k / n if n else 0.0
            ci = wilson(k, n)
            count = w["merged_count"]
            per_window[wname] = {
                "sampled": n,
                "with_type_label": k,
                "share": round(share, 3),
                "share_ci95": [round(c, 3) for c in ci],
                "merged_count": count,
            }
            est += share * count
            lo += ci[0] * count
            hi += ci[1] * count
        repos[repo] = {
            "mapped_labels": len(mapping),
            "windows": per_window,
            "estimated_usable_prs": round(est),
            "estimated_usable_prs_ci95": [round(lo), round(hi)],
            "type_distribution_in_sample": dict(types),
            "sampled_prs_with_multiple_types": multi_type,
        }
        total, total_lo, total_hi = total + est, total_lo + lo, total_hi + hi

    payload = {
        "task": "0.4",
        "step": "estimate",
        "meta": run_metadata(["httpx"]),
        "query_version": QUERY_VERSION,
        "sample_hash": sample_hash(data),
        "mappings_hash": content_hash(mappings),
        "method": (
            "Per repo and window: share of sampled merged PRs with >=1 human-applied label that "
            "maps to a shared type, times the window's merged PR count. 95% Wilson interval per "
            "window, summed (conservative). Sample = most recently created merged PRs per window."
        ),
        "go_threshold": GO_THRESHOLD,
        "total_estimated_usable_prs": round(total),
        "total_ci95": [round(total_lo), round(total_hi)],
        "decision": "go"
        if total_lo >= GO_THRESHOLD
        else ("borderline" if total >= GO_THRESHOLD else "no-go"),
        "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        "repos": repos,
    }
    path = write_result("feasibility_label_volume", payload)
    print("\nrepo | mapped labels | estimated usable PRs (95% CI)")
    for repo, r in repos.items():
        print(
            f"{repo} | {r['mapped_labels']} | "
            f"{r['estimated_usable_prs']} {r['estimated_usable_prs_ci95']}"
        )
    total_line = f"{payload['total_estimated_usable_prs']} {payload['total_ci95']}"
    print(f"\nTOTAL {total_line} -> {payload['decision']}")
    print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["fetch", "estimate"])
    args = parser.parse_args()
    cmd_fetch() if args.step == "fetch" else cmd_estimate()


if __name__ == "__main__":
    main()
