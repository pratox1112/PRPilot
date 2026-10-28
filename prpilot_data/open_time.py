"""Reconstruct a PR's title, body and head commit as they were when it was opened (§4.4).

GitHub's API returns final versions. What it still records about the past:
- every title change is a `RenamedTitleEvent` with `previousTitle`;
- every body edit is a `UserContentEdit`; the oldest one holds the original text;
- the PR's commit list, plus `HeadRefForcePushedEvent`s with the head before each force push.

These are rules about how GitHub stores history, not facts, so they are validated against
GH Archive `opened` payloads (the true open-time state) before being trusted at scale.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

HeadMethod = Literal[
    "last_commit_before_open",
    "before_first_force_push",
    "unresolved",
]

# GraphQL fields for one pull request (used with aliases, several PRs per query).
PR_OPEN_TIME_FIELDS = """
  number title body createdAt baseRefName headRefOid
  userContentEdits(first: 100) { totalCount nodes { createdAt diff } }
  timelineItems(first: 100, itemTypes: [RENAMED_TITLE_EVENT, HEAD_REF_FORCE_PUSHED_EVENT]) {
    totalCount
    nodes {
      __typename
      ... on RenamedTitleEvent { createdAt previousTitle currentTitle }
      ... on HeadRefForcePushedEvent {
        createdAt
        beforeCommit { oid committedDate }
        afterCommit { oid committedDate }
      }
    }
  }
  commits(first: 100) { totalCount nodes { commit { oid committedDate } } }"""


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def normalize_text(text: str | None) -> str:
    """Compare bodies the way a reader would: same line endings, no outer whitespace."""
    return (text or "").replace("\r\n", "\n").strip()


def title_at_open(current_title: str, renames: list[dict]) -> str:
    if not renames:
        return current_title
    first = min(renames, key=lambda r: r["createdAt"])
    return first["previousTitle"]


def body_at_open(current_body: str | None, edits: list[dict]) -> str:
    if not edits:
        return current_body or ""
    oldest = min(edits, key=lambda e: e["createdAt"])
    return oldest.get("diff") or ""


@dataclass(frozen=True)
class HeadAtOpen:
    sha: str | None
    method: HeadMethod


def head_at_open(
    created_at: str,
    commits: list[dict],
    force_pushes: list[dict],
    tolerance: timedelta = timedelta(0),
) -> HeadAtOpen:
    """Head commit when the PR was opened.

    `commits`: the PR's current commit list, oldest first, as {"oid", "committedDate"}.
    `force_pushes`: {"createdAt", "beforeCommit": {"oid", "committedDate"}} events.
    A commit belongs to the open-time head if it was committed no later than the opening
    (plus `tolerance` for clock skew).
    """
    opened = parse_ts(created_at) + tolerance

    def before_open(c: dict | None) -> bool:
        return bool(c and c.get("committedDate")) and parse_ts(c["committedDate"]) <= opened

    pushes = sorted(force_pushes, key=lambda p: p["createdAt"])
    if not pushes:
        earlier = [c for c in commits if before_open(c)]
        if earlier:
            return HeadAtOpen(earlier[-1]["oid"], "last_commit_before_open")
        return HeadAtOpen(None, "unresolved")
    before = pushes[0].get("beforeCommit")
    if before_open(before):
        return HeadAtOpen(before["oid"], "before_first_force_push")
    return HeadAtOpen(None, "unresolved")


def reconstruct(pr: dict, tolerance: timedelta = timedelta(0)) -> dict:
    """Open-time snapshot from one GraphQL pull request node (PR_OPEN_TIME_FIELDS)."""
    timeline = pr["timelineItems"]["nodes"]
    renames = [n for n in timeline if n.get("__typename") == "RenamedTitleEvent"]
    pushes = [n for n in timeline if n.get("__typename") == "HeadRefForcePushedEvent"]
    commits = [n["commit"] for n in pr["commits"]["nodes"]]
    head = head_at_open(pr["createdAt"], commits, pushes, tolerance)
    return {
        "title": title_at_open(pr["title"], renames),
        "body": body_at_open(pr.get("body"), pr["userContentEdits"]["nodes"]),
        "head_sha": head.sha,
        "head_method": head.method,
        "flags": {
            "renamed": bool(renames),
            "body_edited": bool(pr["userContentEdits"]["nodes"]),
            "force_pushed": bool(pushes),
            "edits_capped": pr["userContentEdits"]["totalCount"] > 100,
            "timeline_capped": pr["timelineItems"]["totalCount"] > 100,
            "commits_capped": pr["commits"]["totalCount"] > 100,
        },
    }
