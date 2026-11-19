"""Historical PRs served as of their open time only (hard rule 11), for the shadow replay.

Built from the dataset rows and the open-time candidate lists (prpilot_data). A row holds
both what was known when the PR opened and what happened later (the labels people applied,
reviewers, merge time, linked issues, reverts); this client copies only the open-time fields
listed in OPEN_TIME_FIELDS, so later facts cannot leak through it. Labels and issues come from
the candidate lists, which were built from labels created and issues open before the PR
opened.

A replay bundle (`bundle_record`, one JSON object per line) holds exactly what this client
serves, so the file the deployed worker reads carries no later fact either.
"""

import json
from pathlib import Path

from prpilot_core.github.client import Issue, PRInfo

OPEN_TIME_FIELDS = (
    "repo",
    "number",
    "created_at",
    "author",
    "title",
    "body",
    "base_branch",
    "changed_files",
    "diff",
    "head_sha",
)


class NotInReplay(KeyError):
    pass


def bundle_record(row: dict, candidates: dict) -> dict:
    """One bundle line: a dataset row's open-time fields and its open-time candidate lists."""
    return {
        "pr": {k: row[k] for k in OPEN_TIME_FIELDS},
        "candidates": {
            "repo": row["repo"],
            "number": int(row["number"]),
            "labels": list(candidates["labels"]),
            "reviewers": [{"login": r["login"]} for r in candidates.get("reviewers", [])],
            "issues": [{"number": i["number"], "title": i.get("title") or ""}
                       for i in candidates["issues"]],
        },
    }  # fmt: skip


class ReplayGitHubClient:
    # The candidate lists are already filtered and ranked with the history known at each PR's
    # open time (Phase 2), so the context step uses them as they are instead of re-ranking
    # from today's memory: the prompt then matches the one the student was evaluated on.
    snapshot = True

    def __init__(self, rows: list[dict], candidates: list[dict]):
        self._prs = {
            (r["repo"], int(r["number"])): {k: r[k] for k in OPEN_TIME_FIELDS} for r in rows
        }
        self._labels = {(c["repo"], int(c["number"])): list(c["labels"]) for c in candidates}
        self._reviewers = {
            (c["repo"], int(c["number"])): [r["login"] for r in c.get("reviewers", [])]
            for c in candidates
        }
        self._issues = {
            (c["repo"], int(c["number"])): [Issue(i["number"], i.get("title") or "", "")
                                            for i in c["issues"]]
            for c in candidates
        }  # fmt: skip

    @classmethod
    def from_bundle(cls, path: str | Path) -> "ReplayGitHubClient":
        # Split on "\n" only (PR text can hold other line separators; see prpilot_data.records).
        with open(path, encoding="utf-8", newline="") as f:
            records = [json.loads(line) for line in f.read().split("\n") if line.strip()]
        return cls([r["pr"] for r in records], [r["candidates"] for r in records])

    def __contains__(self, key: tuple[str, int]) -> bool:
        return key in self._prs

    def _pr(self, repo: str, number: int) -> dict:
        try:
            return self._prs[(repo, number)]
        except KeyError as e:
            raise NotInReplay(f"{repo}#{number}") from e

    async def get_pr(self, repo: str, number: int) -> PRInfo:
        r = self._pr(repo, number)
        return PRInfo(repo, number, r["title"], r["body"] or "", r["author"] or "",
                      r["base_branch"], r["head_sha"], r["created_at"])  # fmt: skip

    async def get_diff(self, repo: str, number: int) -> str:
        return self._pr(repo, number)["diff"] or ""

    async def list_changed_files(self, repo: str, number: int) -> list[str]:
        return list(self._pr(repo, number)["changed_files"])

    async def get_repo_labels(self, repo: str, number: int) -> list[str]:
        self._pr(repo, number)
        return list(self._labels.get((repo, number), []))

    async def search_issues(self, repo: str, number: int) -> list[Issue]:
        self._pr(repo, number)
        return list(self._issues.get((repo, number), []))

    async def reviewer_candidates(self, repo: str, number: int) -> list[str]:
        """Eligible reviewers ranked by the path history known at the PR's open time."""
        self._pr(repo, number)
        return list(self._reviewers.get((repo, number), []))
