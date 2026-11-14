"""Smoke suite: 50 validation PRs with recorded replies from three models
(scripts/make_smoke_suite.py). Re-scoring them must give exactly the recorded metrics."""

import json
from pathlib import Path

import pytest

from prpilot_eval.__main__ import score

SMOKE = Path(__file__).parent / "smoke"
EXPECTED = json.loads((SMOKE / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("run", sorted(EXPECTED["runs"]))
def test_recorded_replies_score_as_recorded(run):
    report, records = score(SMOKE / "pack.jsonl", SMOKE / "targets.jsonl", SMOKE / f"{run}.jsonl")
    assert len(records) == 50
    assert report["metrics"] == EXPECTED["runs"][run]["metrics"]
