"""Memory (roadmap §8.4): repo conventions and reviewer history, kept in Postgres.

- Reviewer history: who reviewed merged PRs under which path prefix. It is loaded from the
  training window (source "train") and grows with live outcomes (source "live"). Ranking a
  new PR uses exactly the Phase 2 formula (prpilot_data.candidates): over the PR's path keys
  (directory prefixes, weighted by depth, and the files themselves), sum weight x reviews,
  drop the author, best first. So a live prompt lists reviewers the way training prompts did.
- Label stats: how often people applied each label. A repo with stats offers only labels used
  at least LABEL_MIN_USES times, as in the training prompts; a repo without stats (a new
  sandbox repo) offers all of its labels.
"""

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from prpilot_core.db.models import LabelStat, ReviewerHistory
from prpilot_data.candidates import LABEL_MIN_USES, TOP_REVIEWERS, path_keys


async def reviewer_history(session, repo: str) -> dict[str, Counter]:
    rows = await session.execute(
        select(ReviewerHistory.path_prefix, ReviewerHistory.login, ReviewerHistory.count).where(
            ReviewerHistory.repo == repo
        )
    )
    history: dict[str, Counter] = defaultdict(Counter)
    for prefix, login, n in rows:
        history[prefix][login] += n
    return history


def rank_reviewers(files: list[str], author: str, history: dict[str, Counter]) -> list[dict]:
    scores: Counter = Counter()
    for key, weight in path_keys(files).items():
        for login, n in history.get(key, {}).items():
            scores[login] += weight * n
    scores.pop(author, None)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_REVIEWERS]
    return [{"login": k, "score": v} for k, v in ranked]


REVIEWER_RULES = ("model", "model_then_memory", "memory_then_model", "memory")
DEFAULT_REVIEWER_RULE = "model"  # until the memory ablation (chosen on validation) says otherwise


def combine_reviewers(
    model: list[str], ranked: list[str], rule: str = DEFAULT_REVIEWER_RULE, k: int = 3
) -> list[str]:
    """Final reviewers from the model's picks and the memory ranking (both already limited
    to eligible reviewers, never the author)."""
    if rule == "model":
        order = model
    elif rule == "memory":
        order = ranked
    elif rule == "model_then_memory":
        order = [*model, *ranked]
    elif rule == "memory_then_model":
        order = [*ranked, *model]
    else:
        raise ValueError(rule)
    return list(dict.fromkeys(order))[:k]


async def record_review(session, repo: str, files: list[str], logins: list[str],
                        source: str = "live") -> None:  # fmt: skip
    """Add one merged PR's reviewers to the history (all its path keys)."""
    for key in path_keys(files):
        for login in logins:
            stmt = insert(ReviewerHistory).values(
                repo=repo, path_prefix=key, login=login, source=source, count=1
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_reviewer_hist", set_={"count": ReviewerHistory.count + 1}
            )
            await session.execute(stmt)


async def record_labels(session, repo: str, labels: list[str]) -> None:
    """Count labels that were applied (a live outcome) toward the repo's label stats."""
    for label in labels:
        stmt = insert(LabelStat).values(repo=repo, label=label, count=1)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_label_stat", set_={"count": LabelStat.count + 1}
        )
        await session.execute(stmt)


async def offered_labels(session, repo: str, repo_labels: list[str]) -> tuple[list[str], str]:
    """(labels to offer, which rule applied)."""
    stats = dict(
        (await session.execute(
            select(LabelStat.label, LabelStat.count).where(LabelStat.repo == repo)
        )).all()
    )  # fmt: skip
    if not stats:
        return sorted(repo_labels), "all repo labels (no usage stats)"
    return (
        sorted(lab for lab in repo_labels if stats.get(lab, 0) >= LABEL_MIN_USES),
        f"used at least {LABEL_MIN_USES} times",
    )
