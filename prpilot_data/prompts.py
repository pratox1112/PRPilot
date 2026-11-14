"""Prompt packs: every PR's exact model input, rendered once and hashed (Phase 2).

Each line holds the chat messages (the shared system prompt and the user prompt from
prpilot_core.agents.triage_format) plus the rules the repo validator needs. Every runner
(vLLM on Explorer, Vertex AI, the eval harness) reads these files instead of building
prompts itself, so all models see byte-identical inputs and the eval harness never imports
app code. A pack's SHA-256 ties each result to its inputs.
"""

from prpilot_core.agents.triage_format import SYSTEM_PROMPT, PRContext, build_user_prompt
from prpilot_core.deps import RepoRules

PACK_VERSION = "prompts-v1"


def rules_for(cand: dict) -> RepoRules:
    reviewers = tuple(r["login"] for r in cand["reviewers"])
    return RepoRules(
        labels=frozenset(cand["labels"]),
        eligible_reviewers=frozenset(reviewers),
        open_issues={i["number"]: i["title"] for i in cand["issues"]},
        reviewer_order=reviewers,
        issues_ranked=True,
    )


def pack_row(row: dict, cand: dict) -> dict:
    pr = PRContext(
        title=row["title"],
        body=row["body"] or "",
        author=row["author"] or "",
        base_branch=row["base_branch"],
        changed_files=tuple(row["changed_files"]),
        diff=row["diff"] or "",
    )
    rules = rules_for(cand)
    return {
        "repo": row["repo"],
        "number": row["number"],
        "split": row["split"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(pr, rules)},
        ],
        "rules": {
            "pr_author": row["author"] or "",
            "labels": sorted(rules.labels),
            "eligible_reviewers": list(rules.reviewer_order),
            "open_issues": [i["number"] for i in cand["issues"]],
        },
    }
