"""The read interface the pipeline uses for GitHub, and the records it returns (roadmap §8.2).

Two implementations: `LiveGitHubClient` (the real API, rate-limit aware and cached) and
`ReplayGitHubClient` (historical PRs as of their open time only, hard rule 11). The records
are small and fixed on purpose: they hold only what is known when a PR is opened, so nothing
the pipeline reads can carry a later label, review or issue state.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PRInfo:
    repo: str
    number: int
    title: str
    body: str
    author: str
    base_branch: str
    head_sha: str
    created_at: str  # ISO 8601


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str


class GitHubClient(Protocol):
    async def get_pr(self, repo: str, number: int) -> PRInfo: ...
    async def get_diff(self, repo: str, number: int) -> str: ...
    async def list_changed_files(self, repo: str, number: int) -> list[str]: ...
    async def get_repo_labels(self, repo: str, number: int) -> list[str]:
        """The repo's labels as of the PR's open time (live: now)."""
        ...

    async def search_issues(self, repo: str, number: int) -> list[Issue]:
        """The repo's issues that were open when the PR was opened."""
        ...
