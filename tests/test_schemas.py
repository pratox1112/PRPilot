import json

import pytest
from pydantic import ValidationError

from prpilot_core.schemas import PRType, Risk, TriageOutput, count_sentences, triage_json_schema


def valid_payload(**overrides) -> dict:
    payload = {
        "summary": "Fixes a crash when the config file is empty.",
        "type": "bug",
        "risk": "low",
        "labels": ["kind/bug"],
        "reviewers": ["alice", "bob-smith"],
        "related_issues": [123],
        "rationale": "The diff guards a None dereference. Only one file changes.",
    }
    payload.update(overrides)
    return payload


def parse(**overrides) -> TriageOutput:
    return TriageOutput.model_validate_json(json.dumps(valid_payload(**overrides)))


def test_valid_payload_parses():
    out = parse()
    assert out.type is PRType.BUG
    assert out.risk is Risk.LOW
    assert out.reviewers == ["alice", "bob-smith"]


def test_ci_build_type_value():
    assert parse(type="ci/build").type is PRType.CI_BUILD


def test_empty_lists_allowed():
    out = parse(labels=[], reviewers=[], related_issues=[])
    assert out.labels == [] and out.reviewers == [] and out.related_issues == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "bugfix"},
        {"risk": "critical"},
        {"reviewers": ["a", "b", "c", "d"]},
        {"reviewers": ["alice", "alice"]},
        {"reviewers": ["-alice"]},
        {"reviewers": ["al--ice"]},
        {"reviewers": ["a" * 40]},
        {"labels": ["kind/bug", "kind/bug"]},
        {"labels": [" "]},
        {"related_issues": [0]},
        {"related_issues": ["123"]},
        {"related_issues": [5, 5]},
        {"summary": ""},
        {"summary": "One. Two. Three. Four."},
        {"rationale": "One! Two? Three. Four"},
        {"extra_field": "x"},
    ],
)
def test_invalid_payloads_rejected(overrides):
    with pytest.raises(ValidationError):
        parse(**overrides)


@pytest.mark.parametrize("field", ["summary", "type", "risk", "labels", "reviewers"])
def test_missing_field_rejected(field):
    payload = valid_payload()
    del payload[field]
    with pytest.raises(ValidationError):
        TriageOutput.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("No terminator", 1),
        ("One.", 1),
        ("One. Two", 2),
        ("One. Two! Three?", 3),
        ("Version 1.2.3 is used.", 1),
        ("Wait... really?", 2),
    ],
)
def test_count_sentences(text, expected):
    assert count_sentences(text) == expected


def test_json_schema_export():
    schema = triage_json_schema()
    assert set(schema["required"]) == {
        "summary",
        "type",
        "risk",
        "labels",
        "reviewers",
        "related_issues",
        "rationale",
    }
    assert schema["additionalProperties"] is False
