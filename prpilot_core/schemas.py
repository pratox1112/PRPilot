"""Output schema shared by the teacher, the student, and the app.

This module holds only the static schema: field types, enums, list lengths and
sentence limits. Repo-specific checks (labels exist, reviewers are eligible,
issues exist) depend on runtime data and live in the agent's output validator.
"""

import json
import re
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

MAX_SENTENCES = 3
MAX_REVIEWERS = 3

# Sentence terminator followed by whitespace or end of text. Deliberately simple:
# the same rule is used for scoring, so it only has to be consistent.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")

# GitHub login rules: alphanumerics or single hyphens, no leading/trailing hyphen, max 39.
_GITHUB_LOGIN = r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
_GITHUB_LOGIN_MAX = 39


class PRType(StrEnum):
    BUG = "bug"
    FEATURE = "feature"
    DOCS = "docs"
    REFACTOR = "refactor"
    TEST = "test"
    CI_BUILD = "ci/build"
    DEPENDENCY = "dependency"
    OTHER = "other"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def count_sentences(text: str) -> int:
    """Count sentences; trailing text without a terminator counts as one."""
    stripped = text.strip()
    if not stripped:
        return 0
    ends = list(_SENTENCE_END.finditer(stripped))
    trailing = stripped[ends[-1].end() :].strip() if ends else stripped
    return len(ends) + (1 if trailing else 0)


def _max_sentences(text: str) -> str:
    n = count_sentences(text)
    if n > MAX_SENTENCES:
        raise ValueError(f"at most {MAX_SENTENCES} sentences allowed, got {n}")
    return text


def _unique[T](items: list[T]) -> list[T]:
    if len(set(items)) != len(items):
        raise ValueError("duplicate entries are not allowed")
    return items


ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(_max_sentences)
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
GitHubLogin = Annotated[str, StringConstraints(pattern=_GITHUB_LOGIN, max_length=_GITHUB_LOGIN_MAX)]
IssueNumber = Annotated[int, Field(gt=0)]


class TriageOutput(BaseModel):
    """Structured triage result for one pull request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: ShortText
    type: PRType
    risk: Risk
    labels: Annotated[list[Label], AfterValidator(_unique)]
    reviewers: Annotated[
        list[GitHubLogin], Field(max_length=MAX_REVIEWERS), AfterValidator(_unique)
    ] = Field(description="GitHub logins, ranked best first.")
    related_issues: Annotated[list[IssueNumber], AfterValidator(_unique)]
    rationale: ShortText


def triage_json_schema() -> dict:
    return TriageOutput.model_json_schema()


if __name__ == "__main__":
    print(json.dumps(triage_json_schema(), indent=2))
