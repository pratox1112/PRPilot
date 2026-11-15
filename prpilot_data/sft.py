"""Fine-tuning examples from teacher replies and maintainer targets (Phase 3, roadmap §6.1).

One example per train PR where the teacher's reply is valid (static schema) and its `type`
matches the maintainer type (agreement filter). The teacher supplies `summary`, `risk` and
`rationale`; `labels`, `reviewers` and `related_issues` are overwritten with the maintainer
truth, limited to what the prompt offered:
- labels: the human labels before merge that are in the PR's label list (the targets file);
- reviewers: true reviewers among the eligible candidates, in candidate order, at most 3;
- related_issues: true linked issues among the open-issue candidates, in candidate order.
With `fill=True` (sft-v2-fill) the truth still comes first, but the lists are then topped up
with the teacher's own valid picks (reviewers up to 3), so the student does not learn to
answer with empty lists whenever the truth was not among the candidates.
The result must pass both validation layers, so the student never learns an answer the agent
would reject. The prompt is the pack's messages, unchanged, so training and inference inputs
are identical.
"""

import json
import re

from pydantic import ValidationError

from prpilot_core.agents.triage_format import parse_static, repo_rule_errors, strip_think
from prpilot_core.deps import RepoRules, TriageDeps
from prpilot_core.schemas import MAX_REVIEWERS

SFT_VERSION = "sft-v1"
SFT_VERSION_FILL = "sft-v2-fill"
DROP_REASONS = ("no_type", "no_reply", "invalid", "disagree")


def deps_for(rules: dict) -> TriageDeps:
    return TriageDeps(
        rules=RepoRules(
            labels=frozenset(rules["labels"]),
            eligible_reviewers=frozenset(rules["eligible_reviewers"]),
            open_issues=dict.fromkeys(rules["open_issues"], ""),
        ),
        pr_author=rules["pr_author"],
    )


def _then(first: list, extra: list, allowed) -> list:
    """`first`, then the items of `extra` that are allowed and not already listed."""
    seen = set(first)
    return first + [x for x in extra if x in allowed and x not in seen and not seen.add(x)]


def completion(teacher, target: dict, rules: dict, fill: bool = False) -> dict:
    """The training answer: the teacher's prose fields plus the maintainer's choices."""
    true_reviewers = set(target["reviewers"])
    true_issues = set(target["issues"])
    reviewers = [r for r in rules["eligible_reviewers"] if r in true_reviewers]
    issues = [n for n in rules["open_issues"] if n in true_issues]
    if fill:
        reviewers = _then(reviewers, teacher.reviewers, set(rules["eligible_reviewers"]))
        issues = _then(issues, teacher.related_issues, set(rules["open_issues"]))
    return {
        **teacher.model_dump(mode="json"),
        "labels": list(target["labels"]),
        "reviewers": reviewers[:MAX_REVIEWERS],
        "related_issues": issues,
    }


def mentions_replaced(teacher, final: dict) -> bool:
    """Whether the kept prose names a label or reviewer the teacher chose but the truth
    replaced (the rationale may then argue for an answer the target no longer gives)."""
    text = f"{teacher.summary} {teacher.rationale}"
    dropped = set(teacher.labels) - set(final["labels"])
    dropped |= set(teacher.reviewers) - set(final["reviewers"])
    return any(re.search(rf"(?<![\w/-]){re.escape(x)}(?![\w/-])", text) for x in dropped)


def example(
    pack_row: dict, target: dict, teacher_text: str | None, fill: bool = False
) -> tuple[dict | None, str]:
    """Returns (example, reason): reason is "kept" or one of DROP_REASONS."""
    if target["primary_type"] is None:
        return None, "no_type"
    if teacher_text is None:
        return None, "no_reply"
    try:
        teacher = parse_static(strip_think(teacher_text))
    except ValidationError:
        return None, "invalid"
    if teacher.type.value != target["primary_type"]:
        return None, "disagree"
    rules = pack_row["rules"]
    final = completion(teacher, target, rules, fill)
    checked = parse_static(json.dumps(final))
    errors = repo_rule_errors(checked, deps_for(rules))
    if errors:  # cannot happen: every overwritten field is drawn from the prompt's lists
        raise ValueError(f"{pack_row['repo']}#{pack_row['number']}: {errors}")
    answer = json.dumps(checked.model_dump(mode="json"), ensure_ascii=False)
    return {
        "repo": pack_row["repo"],
        "number": pack_row["number"],
        "messages": [*pack_row["messages"], {"role": "assistant", "content": answer}],
        "type": target["primary_type"],
        "rationale_mentions_replaced": mentions_replaced(teacher, final),
    }, "kept"
