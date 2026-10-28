"""Stage A: find every merged PR to the default branch, opened in the data range.

GitHub search returns at most 1,000 results per query, so the range is walked in weekly
windows and any window that still reports more than 1,000 hits is split in half until it
fits. Results (one JSON line per PR) go to data/interim/index/<owner>__<repo>.jsonl.
"""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from prpilot_data.github_api import GitHubGraphQL

RANGE_START = date(2023, 1, 1)
RANGE_END = date(2026, 3, 31)
SEARCH_LIMIT = 1000
PAGE = 50
QUERY_VERSION = "discover-v1"

RATE = "rateLimit { cost remaining resetAt }"
DEFAULT_BRANCH_QUERY = f"""
query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{ defaultBranchRef {{ name }} }}
  {RATE}
}}"""
SEARCH_QUERY = f"""
query($q: String!, $after: String) {{
  search(query: $q, type: ISSUE, first: {PAGE}, after: $after) {{
    issueCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      ... on PullRequest {{
        number createdAt mergedAt baseRefName isDraft
        author {{ __typename login }}
      }}
    }}
  }}
  {RATE}
}}"""


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    def split(self) -> tuple["Window", "Window"]:
        mid = self.start + (self.end - self.start) // 2
        return Window(self.start, mid), Window(mid + timedelta(days=1), self.end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def weekly_windows(start: date, end: date) -> list[Window]:
    out, cur = [], start
    while cur <= end:
        out.append(Window(cur, min(cur + timedelta(days=6), end)))
        cur += timedelta(days=7)
    return out


def search_q(repo: str, branch: str, w: Window) -> str:
    return f"repo:{repo} is:pr is:merged base:{branch} created:{w.start}..{w.end}"


def default_branch(gh: GitHubGraphQL, repo: str) -> str:
    owner, name = repo.split("/")
    data = gh.query(DEFAULT_BRANCH_QUERY, {"owner": owner, "name": name})
    return data["repository"]["defaultBranchRef"]["name"]


def count(gh: GitHubGraphQL, q: str) -> int:
    return gh.query(SEARCH_QUERY, {"q": q, "after": None})["search"]["issueCount"]


def fetch_window(gh: GitHubGraphQL, q: str) -> list[dict]:
    prs, after = [], None
    while True:
        s = gh.query(SEARCH_QUERY, {"q": q, "after": after})["search"]
        prs += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            return prs
        after = s["pageInfo"]["endCursor"]


def plan_windows(gh: GitHubGraphQL, repo: str, branch: str, windows: list[Window]) -> list[Window]:
    """Split windows until each reports at most SEARCH_LIMIT results."""
    out, todo = [], list(windows)
    while todo:
        w = todo.pop(0)
        if count(gh, search_q(repo, branch, w)) > SEARCH_LIMIT and w.days > 1:
            todo[:0] = list(w.split())
        else:
            out.append(w)
    return out


def discover_repo(gh: GitHubGraphQL, repo: str, start: date, end: date) -> tuple[list[dict], dict]:
    branch = default_branch(gh, repo)
    windows = plan_windows(gh, repo, branch, weekly_windows(start, end))
    seen: dict[int, dict] = {}
    expected = 0
    overfull = []
    for w in windows:
        q = search_q(repo, branch, w)
        n = count(gh, q)
        expected += n
        if n > SEARCH_LIMIT:
            overfull.append(str(w.start))
        for pr in fetch_window(gh, q):
            if start.isoformat() <= pr["createdAt"][:10] <= end.isoformat():
                seen[pr["number"]] = {"repo": repo, **pr}
    rows = sorted(seen.values(), key=lambda r: r["number"])
    stats = {
        "default_branch": branch,
        "windows": len(windows),
        "search_reported": expected,
        "fetched_unique": len(rows),
        "overfull_days": overfull,
        "off_branch": sum(r["baseRefName"] != branch for r in rows),
        "by_month": dict(sorted(Counter(r["createdAt"][:7] for r in rows).items())),
    }
    return rows, stats


def write_index(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
