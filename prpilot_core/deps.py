"""Runtime dependencies passed to agents through `RunContext`."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepoRules:
    """Repo-specific facts shown in the prompt and checked by the output validator."""

    labels: frozenset[str]
    eligible_reviewers: frozenset[str]
    open_issues: Mapping[int, str] = field(default_factory=dict)
    # Display order for the prompt (best first). Empty / False: the lists are shown sorted.
    reviewer_order: tuple[str, ...] = ()
    issues_ranked: bool = False


@dataclass(frozen=True)
class TriageDeps:
    """Everything a triage run needs besides the prompt.

    Grows later (GitHub client, memory, db); for now only what validation needs.
    """

    rules: RepoRules
    pr_author: str
    run_id: str = ""
