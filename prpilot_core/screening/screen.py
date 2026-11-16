"""The `screen` step: runs on every PR before any model sees it (roadmap §7.1).

1. PromptGuard 2 scores each model-visible field (title, body as the prompt shows it,
   changed file names, diff) by its most suspicious window. If any field reaches the
   threshold, the PR is quarantined: no model call, no proposals.
2. Otherwise PII is redacted from the prose fields (Presidio) and secrets from the diff
   (detect-secrets), and the redacted PR goes on to the agent.

Screening is not the only defence: PR content still sits in delimited data blocks, and every
write stays behind human approval. The scanners are passed in, so this module imports none
of the heavy screening dependencies and tests can use fakes.
"""

from dataclasses import dataclass, replace
from typing import Protocol

from prpilot_core.agents.triage_format import PRContext, _cap_body, _file_list
from prpilot_core.screening.prompt_guard import DEFAULT_THRESHOLD
from prpilot_core.screening.secrets import Finding, redact_diff, scan_diff


class Guard(Protocol):
    def score(self, text: str): ...  # -> GuardResult (needs only `.score`)


class Redactor(Protocol):
    def redact(self, text: str) -> tuple[str, list]: ...


def guard_fields(pr: PRContext) -> dict[str, str]:
    """The text the model will see, field by field: body and file list capped exactly as the
    triage prompt caps them (the full list of a vendoring PR can exceed 150k characters)."""
    return {
        "title": pr.title,
        "body": _cap_body(pr.body),
        "changed_files": _file_list(pr.changed_files),
        "diff": pr.diff,
    }


@dataclass(frozen=True)
class ScreenResult:
    quarantined: bool
    guard_score: float  # max over fields
    field_scores: dict[str, float]
    pr: PRContext | None  # redacted PR for the agent; None when quarantined
    pii: dict[str, int]  # entity -> count redacted
    secrets: tuple[Finding, ...]


def quarantined(field_scores: dict[str, float], threshold: float = DEFAULT_THRESHOLD) -> bool:
    return max(field_scores.values(), default=0.0) >= threshold


def screen(
    pr: PRContext,
    guard: Guard,
    pii: Redactor | None = None,
    scan_secrets: bool = True,
    threshold: float = DEFAULT_THRESHOLD,
) -> ScreenResult:
    scores = {
        name: float(guard.score(text).score) if text else 0.0
        for name, text in guard_fields(pr).items()
    }
    top = max(scores.values(), default=0.0)
    if quarantined(scores, threshold):
        return ScreenResult(True, top, scores, None, {}, ())
    counts: dict[str, int] = {}
    title, body = pr.title, pr.body
    if pii is not None:
        title, t_spans = pii.redact(title)
        body, b_spans = pii.redact(body)
        for s in [*t_spans, *b_spans]:
            counts[s.entity] = counts.get(s.entity, 0) + 1
    findings = tuple(scan_diff(pr.diff)) if scan_secrets and pr.diff else ()
    diff = redact_diff(pr.diff, list(findings)) if findings else pr.diff
    return ScreenResult(
        False, top, scores, replace(pr, title=title, body=body, diff=diff), counts, findings
    )
