"""Static schema check of one reply, without app code.

The JSON Schema is exported from prpilot_core's `TriageOutput` (a root test keeps the copy
identical). Pydantic also enforces rules a JSON Schema cannot express: strings are stripped
before the length check, at most 3 sentences per text field, no duplicate list entries,
strict integers. Those are re-implemented here; a root test checks both validators agree.
"""

import json
import re
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).with_name("triage_output.schema.json")
MAX_SENTENCES = 3
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")  # the same rule as prpilot_core.schemas


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


def count_sentences(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ends = list(_SENTENCE_END.finditer(stripped))
    trailing = stripped[ends[-1].end() :].strip() if ends else stripped
    return len(ends) + (1 if trailing else 0)


def strip_think(text: str) -> str:
    """Thinking-mode models put reasoning in <think>...</think> before the answer."""
    return text.split("</think>", 1)[1] if "</think>" in text else text


def _extra_errors(obj: dict) -> list[str]:
    errors = []
    for field in ("summary", "rationale"):
        text = obj[field]
        if not text.strip():
            errors.append(f"{field}: empty after stripping")
        elif count_sentences(text) > MAX_SENTENCES:
            errors.append(f"{field}: more than {MAX_SENTENCES} sentences")
    for field in ("labels", "reviewers", "related_issues"):
        items = obj[field]
        if field == "labels":
            if any(not s.strip() for s in items):
                errors.append("labels: empty label after stripping")
            items = [s.strip() for s in items]
        if field == "related_issues" and any(type(n) is not int for n in items):
            errors.append("related_issues: not strict integers")
        if len(set(map(json.dumps, items))) != len(items):
            errors.append(f"{field}: duplicate entries")
    return errors


def check(text: str) -> tuple[dict | None, str | None]:
    """Returns (parsed reply, None) when valid, else (None, error kind: detail)."""
    try:
        obj = json.loads(strip_think(text).strip())
    except json.JSONDecodeError as e:
        return None, f"json: {e.msg}"
    errors = [e.message for e in _validator().iter_errors(obj)]
    if not errors and isinstance(obj, dict):
        errors = _extra_errors(obj)
    if errors:
        return None, "schema: " + "; ".join(sorted(errors))[:300]
    obj["labels"] = [s.strip() for s in obj["labels"]]
    return obj, None
