"""Does reviewer memory help? (roadmap §8.4) Reviewer recall@3 with and without it.

"Memory" is the deterministic `rank_reviewers` step: the reviewers ranked by path history
(the prompt's eligible-reviewer list, best first) combined with the student's own picks by
one of memory.REVIEWER_RULES. No model is run again: each rule rewrites only the `reviewers`
field of the student's existing system-pass replies, after the same validation fallback the
pipeline applies, and the eval harness scores the result exactly as it scored the student.
A reply that cannot be parsed stays as it is (the app proposes nothing for it).

The rule is chosen on validation and applied to the test split once.
"""

import json

from prpilot_core import memory
from prpilot_core.agents.fallback import salvage
from prpilot_core.deps import RepoRules, TriageDeps


def deps_for(rules: dict) -> TriageDeps:
    return TriageDeps(
        rules=RepoRules(
            labels=frozenset(rules["labels"]),
            eligible_reviewers=frozenset(rules["eligible_reviewers"]),
            open_issues=dict.fromkeys(rules["open_issues"], ""),
        ),
        pr_author=rules["pr_author"],
    )


def apply_rule(reply: dict, rules: dict, rule: str) -> dict:
    got = salvage(reply.get("text") or "", deps_for(rules))
    if got is None:
        return reply
    out, _ = got
    ranked = [r for r in rules["eligible_reviewers"] if r != rules["pr_author"]]
    reviewers = memory.combine_reviewers(list(out.reviewers), ranked, rule)
    final = out.model_copy(update={"reviewers": reviewers})
    return {**reply, "text": json.dumps(final.model_dump(mode="json"), ensure_ascii=False)}
