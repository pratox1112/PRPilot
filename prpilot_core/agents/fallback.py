"""Salvaging a reply that still breaks the repo rules after the agent's retries (owner
decision, 2026-11-17).

The student sometimes keeps naming labels that are not in the repo's list (30% of numpy PRs
on the test split; it repeats the same answer after the error message). Instead of
proposing nothing, the last reply is parsed against the static schema and every label,
reviewer or issue outside the prompt's lists (or the PR author as reviewer) is dropped. The
proposal is flagged "partly filtered" with what was removed, and a human still approves
every action. A reply that fails the static schema itself is not salvaged.
"""

from pydantic import ValidationError

from prpilot_core.agents.triage_format import parse_static, strip_think
from prpilot_core.deps import TriageDeps
from prpilot_core.schemas import TriageOutput


def filter_to_rules(out: TriageOutput, deps: TriageDeps) -> tuple[TriageOutput, dict]:
    rules = deps.rules
    keep_labels = [x for x in out.labels if x in rules.labels]
    keep_reviewers = [
        x for x in out.reviewers if x in rules.eligible_reviewers and x != deps.pr_author
    ]
    keep_issues = [x for x in out.related_issues if x in rules.open_issues]
    removed = {
        "labels": [x for x in out.labels if x not in keep_labels],
        "reviewers": [x for x in out.reviewers if x not in keep_reviewers],
        "related_issues": [x for x in out.related_issues if x not in keep_issues],
    }
    filtered = out.model_copy(
        update={"labels": keep_labels, "reviewers": keep_reviewers, "related_issues": keep_issues}
    )
    return filtered, {k: v for k, v in removed.items() if v}


def salvage(text: str, deps: TriageDeps) -> tuple[TriageOutput, dict] | None:
    try:
        out = parse_static(strip_think(text))
    except ValidationError:
        return None
    return filter_to_rules(out, deps)
