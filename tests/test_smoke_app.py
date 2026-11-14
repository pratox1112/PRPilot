"""Smoke suite, app side: the triage validator (both layers) must judge the 50 recorded replies
exactly as when the suite was built, and its static layer must agree with the eval harness."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from prpilot_core.agents.triage_format import parse_static, repo_rule_errors, strip_think
from prpilot_core.deps import RepoRules, TriageDeps

SMOKE = Path(__file__).resolve().parents[1] / "eval" / "tests" / "smoke"
EXPECTED = json.loads((SMOKE / "expected.json").read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def verdict(text: str, rules: dict) -> dict:
    deps = TriageDeps(
        rules=RepoRules(
            labels=frozenset(rules["labels"]),
            eligible_reviewers=frozenset(rules["eligible_reviewers"]),
            open_issues=dict.fromkeys(rules["open_issues"], ""),
        ),
        pr_author=rules["pr_author"],
    )
    try:
        out = parse_static(strip_think(text))
    except ValidationError:
        return {"static": False, "repo_rules": False}
    return {"static": True, "repo_rules": not repo_rule_errors(out, deps)}


@pytest.mark.parametrize("run", sorted(EXPECTED["runs"]))
def test_app_validator_matches_recorded_verdicts(run):
    rules = {(p["repo"], p["number"]): p["rules"] for p in read_jsonl(SMOKE / "pack.jsonl")}
    replies = read_jsonl(SMOKE / f"{run}.jsonl")
    got = [verdict(r["text"], rules[(r["repo"], r["number"])]) for r in replies]
    expected = EXPECTED["runs"][run]
    assert got == expected["app_verdicts"]
    static_rate = sum(v["static"] for v in got) / len(got)
    assert static_rate == expected["metrics"]["json_validity"]["value"]
