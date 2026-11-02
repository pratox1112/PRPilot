"""Issues and labels per repo, for the open-time candidate lists (Phase 2).

A triage model picks related issues and labels from lists it is given; to score it on
historical PRs those lists must be what existed when the PR was opened. Every issue (any
state) is fetched with its creation and closing time, so "open at time t" can be decided
later; labels come with their creation time. Read-only, cached, resumable page by page.

The title and body are today's (GitHub keeps no issue edit history in this query); the body
is cut to BODY_CHARS, enough for keyword search. Pull requests are not issues here.
"""

import json
from pathlib import Path

from prpilot_data.github_api import GitHubGraphQL

QUERY_VERSION = "issues-v1"
PAGE = 50
BODY_CHARS = 2000

ISSUES_QUERY = f"""query($owner: String!, $name: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: {PAGE}, after: $after, orderBy: {{field: CREATED_AT, direction: ASC}}) {{
      totalCount
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ number title body createdAt closedAt author {{ __typename login }} }}
    }}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""

LABELS_QUERY = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    labels(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { name createdAt }
    }
  }
  rateLimit { cost remaining resetAt }
}"""


def issue_row(node: dict) -> dict:
    return {
        "number": node["number"],
        "title": node["title"],
        "body": (node.get("body") or "")[:BODY_CHARS],
        "created_at": node["createdAt"],
        "closed_at": node.get("closedAt"),
        "author": (node.get("author") or {}).get("login"),
    }


def fetch_issues(
    gh: GitHubGraphQL, repo: str, out: Path, max_pages: int | None = None
) -> tuple[int, int]:
    """Writes every issue to `out` (one JSON line each). Returns (written, GitHub's total).
    Pages come from the cache when already fetched, so an interrupted run resumes cheaply."""
    owner, name = repo.split("/")
    out.parent.mkdir(parents=True, exist_ok=True)
    after, pages, written, total = None, 0, 0, 0
    with out.open("w", encoding="utf-8", newline="") as f:
        while True:
            data = gh.query(ISSUES_QUERY, {"owner": owner, "name": name, "after": after})
            issues = data["repository"]["issues"]
            total = issues["totalCount"]
            for node in issues["nodes"]:
                if node:
                    f.write(json.dumps(issue_row(node), ensure_ascii=False) + "\n")
                    written += 1
            pages += 1
            if pages % 100 == 0:
                print(f"  {repo}: {written}/{total} issues")
            if not issues["pageInfo"]["hasNextPage"] or (max_pages and pages >= max_pages):
                break
            after = issues["pageInfo"]["endCursor"]
    return written, total


def fetch_labels(gh: GitHubGraphQL, repo: str) -> list[dict]:
    owner, name = repo.split("/")
    after, labels = None, []
    while True:
        data = gh.query(LABELS_QUERY, {"owner": owner, "name": name, "after": after})
        page = data["repository"]["labels"]
        labels += [{"name": n["name"], "created_at": n["createdAt"]} for n in page["nodes"] if n]
        if not page["pageInfo"]["hasNextPage"]:
            return sorted(labels, key=lambda label: label["name"])
        after = page["pageInfo"]["endCursor"]
