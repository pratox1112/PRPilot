"""Deterministic context prefetch: everything the triage prompt shows, gathered before any
model call (roadmap §8.2). The student is not asked to call tools; it gets this context in
the prompt, built exactly as the training prompts were:

- the diff is fitted to the same 6,000-token budget with the student's own tokenizer, and the
  file list comes from the full diff (prpilot_data.inputs);
- labels, eligible reviewers and open issues are the prompt's three lists. For a live PR they
  come from the repo and from memory (labels filtered by usage, reviewers ranked by path
  history, issues ranked by the Phase 2 BM25 scorer with the issues the PR names first). For
  a replayed PR they are the open-time candidate lists, used as they are.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache

from prpilot_core import memory
from prpilot_core.agents.triage_format import PRContext
from prpilot_core.deps import RepoRules
from prpilot_core.github.client import GitHubClient, PRInfo
from prpilot_data.candidates import TOP_ISSUES, IssueIndex, named_issues
from prpilot_data.diffs import DIFF_TOKENS
from prpilot_data.inputs import changed_files, fit_diff_to_tokens

FAR_PAST = "2000-01-01T00:00:00Z"  # live issues: all open now, all count for term stats


@dataclass(frozen=True)
class Context:
    info: PRInfo
    pr: PRContext
    rules: RepoRules
    notes: dict = field(default_factory=dict)


@cache
def _count_tokens():
    from prpilot_data.tokens import load_counter

    return load_counter()


def rank_live_issues(info: PRInfo, files: list[str], issues) -> list[tuple[int, str]]:
    if not issues:
        return []
    index = IssueIndex(
        [{"number": i.number, "title": i.title, "body": i.body, "created_at": FAR_PAST,
          "closed_at": None} for i in issues]
    )  # fmt: skip
    query = " ".join([info.title, info.body, " ".join(files)])
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    named = named_issues(f"{info.title}\n{info.body}", info.repo)
    return [(i["number"], index.title(i["number"])) for i in index.rank(query, now, named)]


async def build_context(gh: GitHubClient, session, repo: str, number: int,
                        count_tokens=None) -> Context:  # fmt: skip
    info = await gh.get_pr(repo, number)
    full_diff = await gh.get_diff(repo, number)
    snapshot = getattr(gh, "snapshot", False)
    if snapshot:  # already the stored open-time diff and file list
        diff, files = full_diff, await gh.list_changed_files(repo, number)
        truncated = None
    else:
        files = list(changed_files(full_diff)) or await gh.list_changed_files(repo, number)
        diff, truncated, _ = fit_diff_to_tokens(full_diff, DIFF_TOKENS,
                                                count_tokens or _count_tokens())  # fmt: skip
    repo_labels = await gh.get_repo_labels(repo, number)
    issues = await gh.search_issues(repo, number)
    if snapshot:
        labels, label_rule = sorted(repo_labels), "open-time candidate list"
        reviewers = await gh.reviewer_candidates(repo, number)
        ranked_issues = [(i.number, i.title) for i in issues][:TOP_ISSUES]
    else:
        labels, label_rule = await memory.offered_labels(session, repo, repo_labels)
        history = await memory.reviewer_history(session, repo)
        reviewers = [r["login"] for r in memory.rank_reviewers(files, info.author, history)]
        ranked_issues = rank_live_issues(info, files, issues)
    rules = RepoRules(
        labels=frozenset(labels),
        eligible_reviewers=frozenset(reviewers),
        open_issues=dict(ranked_issues),
        reviewer_order=tuple(reviewers),
        issues_ranked=True,
    )
    pr = PRContext(info.title, info.body, info.author, info.base_branch, tuple(files), diff)
    notes = {"label_rule": label_rule, "diff_truncated": truncated, "snapshot": snapshot}
    return Context(info, pr, rules, notes)
