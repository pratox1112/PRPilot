"""Proposals: the only thing the pipeline produces, and the only thing a human approves.

A proposal is a list of actions (add labels, request reviewers, post a comment), each with
its own id so it can be approved or rejected separately. Its content is frozen by a hash:
SHA-256 of the canonical JSON. An approval records the hash the human saw, and the executor
re-checks both, so editing a proposal after it was shown, or after it was approved, makes it
unexecutable.
"""

import hashlib
import json

from prpilot_core.schemas import TriageOutput

PROPOSAL_VERSION = 1


def content_hash(content: dict) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_content(
    repo: str,
    pr_number: int,
    head_sha: str,
    triage: TriageOutput,
    comment: str,
    notes: dict | None = None,
) -> dict:
    """Actions are included only when they would do something (no empty label list)."""
    actions = []
    if triage.labels:
        actions.append({"id": "labels", "type": "add_labels", "labels": list(triage.labels)})
    if triage.reviewers:
        actions.append(
            {"id": "reviewers", "type": "request_reviewers", "reviewers": list(triage.reviewers)}
        )
    if comment:
        actions.append({"id": "comment", "type": "post_comment", "body": comment})
    return {
        "version": PROPOSAL_VERSION,
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "actions": actions,
        "triage": triage.model_dump(mode="json"),
        "notes": notes or {},
    }


def action(content: dict, action_id: str) -> dict | None:
    return next((a for a in content.get("actions", []) if a["id"] == action_id), None)
