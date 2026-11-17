"""Promptfoo provider: runs PRPilot's screening step on one red-team case (roadmap §7.4).

The output is what would reach the triage model: `QUARANTINED` when the screen blocks the
PR, otherwise the user prompt the agent would send (built from the redacted PR). The suite
asserts the case's canary does not appear in that output, so a case fails exactly when an
injected PR would get through to the model. No model runs in CI.

PRPILOT_SCREENING=off skips the screen (the one-time check that the suite fails without it).
"""

import os
from functools import cache

from prpilot_core.agents.triage_format import PRContext, build_user_prompt
from prpilot_core.deps import RepoRules
from prpilot_core.screening.prompt_guard import PROMPT_GUARD_86M, PromptGuard
from prpilot_core.screening.screen import screen

RULES = RepoRules(
    labels=frozenset({"kind/bug", "kind/feature", "area/docs"}),
    eligible_reviewers=frozenset({"maintainer-a", "maintainer-b"}),
    open_issues={},
)


@cache
def guard() -> PromptGuard:
    return PromptGuard(PROMPT_GUARD_86M, token=os.environ.get("HF_TOKEN") or None)


def call_api(prompt, options, context):
    v = context["vars"]["pr"]
    pr = PRContext(
        title=v["title"],
        body=v["body"],
        author=v["author"],
        base_branch=v["base_branch"],
        changed_files=tuple(v["changed_files"]),
        diff=v["diff"],
    )
    if os.environ.get("PRPILOT_SCREENING") == "off":
        return {"output": build_user_prompt(pr, RULES)}
    res = screen(pr, guard(), pii=None, scan_secrets=True)
    if res.quarantined:
        return {"output": f"QUARANTINED (PromptGuard {res.guard_score:.3f})"}
    return {"output": build_user_prompt(res.pr, RULES)}
