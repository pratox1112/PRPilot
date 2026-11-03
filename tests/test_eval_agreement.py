"""The eval harness re-implements the static schema check without importing app code.
These tests keep the two in step: the exported JSON Schema must equal TriageOutput's, and
both validators must accept and reject the same replies."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from prpilot_core.agents.triage_format import parse_static, strip_think
from prpilot_core.schemas import triage_json_schema

EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL))
pytest.importorskip("jsonschema")
from prpilot_eval.validate import SCHEMA_FILE, check  # noqa: E402

GOOD = {
    "summary": "Fixes a crash.",
    "type": "bug",
    "risk": "low",
    "labels": ["kind/bug"],
    "reviewers": ["bob"],
    "related_issues": [12],
    "rationale": "The diff guards a nil pointer.",
}
CASES = [
    GOOD,
    {**GOOD, "labels": [" kind/bug "]},
    {**GOOD, "labels": ["a", " a"]},
    {**GOOD, "labels": ["  "]},
    {**GOOD, "summary": "One. Two. Three."},
    {**GOOD, "summary": "One. Two. Three. Four."},
    {**GOOD, "summary": "  "},
    {**GOOD, "reviewers": ["bob", "bob"]},
    {**GOOD, "reviewers": ["a", "b", "c", "d"]},
    {**GOOD, "reviewers": ["dependabot[bot]"]},
    {**GOOD, "related_issues": [12.0]},
    {**GOOD, "related_issues": [0]},
    {**GOOD, "related_issues": [True]},
    {**GOOD, "type": "security"},
    {**GOOD, "extra": 1},
    {k: v for k, v in GOOD.items() if k != "risk"},
]


def test_exported_schema_matches_triage_output():
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == triage_json_schema()


@pytest.mark.parametrize("case", CASES)
def test_eval_and_app_validators_agree(case):
    text = json.dumps(case)
    try:
        parse_static(strip_think(text))
        app_ok = True
    except ValidationError:
        app_ok = False
    assert (check(text)[1] is None) == app_ok
