"""Task 0.5: how many test-set PRs were later reverted (risk ground truth, roadmap §4.3)?

For each selected repo, finds merged PRs with "revert" in the title from 2023-01-01 until
today, links each to the PR it reverts, and counts reverted originals per split window.
Test set = the newest window of time-split repos + every window of the held-out repo.

Usage:
    uv run python scripts/feasibility/reverts.py
"""

import tomllib
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from common import REPO_ROOT, content_hash, run_metadata, write_result
from dotenv import load_dotenv
from revert_links import extract_refs, is_revert_title, title_matches

from prpilot_data.github_api import GitHubGraphQL

QUERY_VERSION = "reverts-v1"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "github_graphql"
REPOS_CONFIG = REPO_ROOT / "data" / "config" / "repos.toml"
RANGE_START, RANGE_END = "2023-01-01", "2026-03-31"
WINDOWS = [  # provisional split windows, by merge date (same as task 0.4)
    ("train", "2023-01-01", "2024-12-31"),
    ("val", "2025-01-01", "2025-06-30"),
    ("test", "2025-07-01", "2026-03-31"),
]
GO_THRESHOLD = 50

RATE = "rateLimit { cost remaining resetAt }"
PR_FIELDS = "number title createdAt mergedAt merged baseRefName"
SEARCH_QUERY = f"""
query($q: String!, $after: String) {{
  search(query: $q, type: ISSUE, first: 50, after: $after) {{
    issueCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      ... on PullRequest {{
        {PR_FIELDS} body
        commits(first: 10) {{ nodes {{ commit {{ message }} }} }}
      }}
    }}
  }}
  {RATE}
}}"""
ISSUE_QUERY = f"""
query($owner: String!, $name: String!, $n: Int!) {{
  repository(owner: $owner, name: $name) {{
    issueOrPullRequest(number: $n) {{ __typename ... on PullRequest {{ {PR_FIELDS} }} }}
  }}
  {RATE}
}}"""
SHA_QUERY = f"""
query($owner: String!, $name: String!, $sha: String!) {{
  repository(owner: $owner, name: $name) {{
    defaultBranchRef {{ name }}
    object(expression: $sha) {{
      ... on Commit {{ associatedPullRequests(first: 5) {{ nodes {{ {PR_FIELDS} }} }} }}
    }}
  }}
  {RATE}
}}"""
COUNT_QUERY = f"""
query($q: String!) {{ search(query: $q, type: ISSUE, first: 1) {{ issueCount }} {RATE} }}"""
DEFAULT_BRANCH_QUERY = f"""
query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{ defaultBranchRef {{ name }} }}
  {RATE}
}}"""


def half_years(start: str, end: date) -> list[tuple[str, str]]:
    out, y, h = [], int(start[:4]), 1
    while date(y, 1 if h == 1 else 7, 1) <= end:
        s = date(y, 1 if h == 1 else 7, 1)
        e = min(date(y, 6, 30) if h == 1 else date(y, 12, 31), end)
        out.append((s.isoformat(), e.isoformat()))
        y, h = (y, 2) if h == 1 else (y + 1, 1)
    return out


def window_of(merged_at: str) -> str | None:
    day = merged_at[:10]
    for name, start, end in WINDOWS:
        if start <= day <= end:
            return name
    return None


class Resolver:
    def __init__(self, gh: GitHubGraphQL, repo: str):
        self.gh, self.repo = gh, repo
        self.owner, self.name = repo.split("/")

    def pr(self, number: int) -> dict | None:
        v = {"owner": self.owner, "name": self.name, "n": number}
        node = self.gh.query(ISSUE_QUERY, v)["repository"]["issueOrPullRequest"]
        return node if node and node.get("__typename") == "PullRequest" else None

    def prs_for_sha(self, sha: str) -> list[dict]:
        v = {"owner": self.owner, "name": self.name, "sha": sha}
        obj = self.gh.query(SHA_QUERY, v)["repository"]["object"]
        return (obj or {}).get("associatedPullRequests", {}).get("nodes", []) if obj else []


def find_original(revert: dict, res: Resolver) -> tuple[dict | None, str]:
    msgs = [n["commit"]["message"] for n in revert["commits"]["nodes"]]
    refs = extract_refs(res.repo, revert["title"], revert["body"], msgs)

    def usable(pr: dict | None) -> bool:
        return bool(
            pr
            and pr["merged"]
            and pr["number"] != revert["number"]
            and pr["mergedAt"] <= revert["mergedAt"]
        )

    for n in sorted(refs.explicit_prs):
        pr = res.pr(n)
        if usable(pr):
            return pr, "explicit_ref"
    for sha in sorted(refs.commit_shas):
        for pr in res.prs_for_sha(sha):
            if usable(pr):
                return pr, "commit_sha"
    for n in sorted(refs.candidate_prs):
        pr = res.pr(n)
        if usable(pr) and title_matches(revert["title"], pr["title"]):
            return pr, "title_match"
    return None, "unlinked"


def scan_repo(gh: GitHubGraphQL, repo: str, until: date) -> dict:
    res = Resolver(gh, repo)
    owner, name = repo.split("/")
    default_branch = gh.query(DEFAULT_BRANCH_QUERY, {"owner": owner, "name": name})["repository"][
        "defaultBranchRef"
    ]["name"]

    reverts = []
    for start, end in half_years(RANGE_START, until):
        q = f"repo:{repo} is:pr is:merged in:title revert merged:{start}..{end}"
        after = None
        while True:
            s = gh.query(SEARCH_QUERY, {"q": q, "after": after})["search"]
            if s["issueCount"] > 1000:
                raise SystemExit(f"{repo} {start}..{end}: >1000 results, split the window")
            reverts += [n for n in s["nodes"] if n and is_revert_title(n["title"])]
            if not s["pageInfo"]["hasNextPage"]:
                break
            after = s["pageInfo"]["endCursor"]
    print(f"  {repo}: {len(reverts)} revert PRs")

    originals: dict[int, dict] = {}
    methods: Counter = Counter()
    revert_of_revert = 0
    for r in reverts:
        orig, method = find_original(r, res)
        methods[method] += 1
        if orig is None:
            continue
        if is_revert_title(orig["title"]):
            revert_of_revert += 1
            continue
        originals.setdefault(
            orig["number"],
            {
                "number": orig["number"],
                "merged_at": orig["mergedAt"],
                "base": orig["baseRefName"],
                "on_default_branch": orig["baseRefName"] == default_branch,
                "window": window_of(orig["mergedAt"]),
                "reverted_by": r["number"],
                "link": method,
            },
        )

    merged_counts = {
        w: gh.query(COUNT_QUERY, {"q": f"repo:{repo} is:pr is:merged merged:{s}..{e}"})["search"][
            "issueCount"
        ]
        for w, s, e in WINDOWS
    }
    by_window: dict[str, Counter] = defaultdict(Counter)
    for o in originals.values():
        if o["window"]:
            by_window[o["window"]]["all"] += 1
            by_window[o["window"]]["default_branch"] += o["on_default_branch"]
    return {
        "default_branch": default_branch,
        "revert_prs_found": len(reverts),
        "link_methods": dict(methods),
        "revert_of_revert_skipped": revert_of_revert,
        "merged_counts": merged_counts,
        "positives_by_window": {w: dict(by_window[w]) for w, _, _ in WINDOWS},
        "originals": sorted(originals.values(), key=lambda o: o["number"]),
    }


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    cfg = tomllib.loads(REPOS_CONFIG.read_text(encoding="utf-8"))["repos"]
    selected = {r: c["split_role"] for r, c in cfg.items() if c.get("status") == "selected"}
    gh = GitHubGraphQL(CACHE_DIR, QUERY_VERSION)
    until = datetime.now(UTC).date()

    repos = {}
    for repo in selected:
        print(f"scanning {repo}")
        repos[repo] = scan_repo(gh, repo, until)

    test_all = test_default = 0
    test_n = 0
    for repo, role in selected.items():
        r = repos[repo]
        windows = [w for w, _, _ in WINDOWS] if role == "held_out" else ["test"]
        r["test_windows"] = windows
        r["test_positives"] = sum(r["positives_by_window"][w].get("all", 0) for w in windows)
        r["test_positives_default_branch"] = sum(
            r["positives_by_window"][w].get("default_branch", 0) for w in windows
        )
        r["test_merged_prs"] = sum(r["merged_counts"][w] for w in windows)
        test_all += r["test_positives"]
        test_default += r["test_positives_default_branch"]
        test_n += r["test_merged_prs"]

    payload = {
        "task": "0.5",
        "meta": run_metadata(["httpx"]),
        "query_version": QUERY_VERSION,
        "revert_search_until": until.isoformat(),
        "windows": WINDOWS,
        "split_roles": selected,
        "method": (
            "Revert PR = merged PR whose title starts with revert/reverts/reverted/reverting "
            "(optionally after [tags]). Linked to its original via 'Reverts owner/repo#N' or "
            "'revert ... #N/URL' (explicit_ref), 'This reverts commit <sha>' (commit_sha), or a "
            "bare #N whose title matches the revert title (title_match). Original must be merged "
            "before the revert. Reverts of reverts are skipped. Window = original's merge date. "
            "Direct-push reverts without a PR are not found."
        ),
        "go_threshold": GO_THRESHOLD,
        "test_set_merged_prs": test_n,
        "test_positives": test_all,
        "test_positives_default_branch_only": test_default,
        "test_positive_rate": round(test_all / test_n, 4) if test_n else None,
        "decision": "go" if test_all >= GO_THRESHOLD else "no-go",
        "positives_hash": content_hash(
            {r: [o["number"] for o in v["originals"]] for r, v in repos.items()}
        ),
        "github_calls": {"network": gh.calls, "cache_hits": gh.cache_hits},
        "repos": repos,
    }
    path = write_result("feasibility_reverts", payload)
    print("\nrepo | revert PRs | links | positives train/val/test | test-set positives")
    for repo, r in repos.items():
        pw = r["positives_by_window"]
        print(
            f"{repo} | {r['revert_prs_found']} | {r['link_methods']} | "
            f"{pw['train'].get('all', 0)}/{pw['val'].get('all', 0)}/{pw['test'].get('all', 0)} | "
            f"{r['test_positives']} ({r['test_positives_default_branch']} on default branch)"
        )
    print(
        f"\nTEST SET: {test_all} positives ({test_default} on default branch) out of "
        f"{test_n} merged PRs -> {payload['decision']}"
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
