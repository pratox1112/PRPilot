import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

from score_agreement import score_mode, teacher_type, wilson  # noqa: E402


def reply(type_: str) -> str:
    return json.dumps(
        {
            "summary": "Does a thing.",
            "type": type_,
            "risk": "low",
            "labels": [],
            "reviewers": [],
            "related_issues": [],
            "rationale": "Because.",
        }
    )


def test_teacher_type_parses_and_strips_think():
    assert teacher_type(reply("bug")) == "bug"
    assert teacher_type("<think>hmm</think>\n" + reply("docs")) == "docs"
    assert teacher_type("```json\n" + reply("bug") + "\n```") is None
    assert teacher_type("not json") is None


def test_score_mode_counts_invalid_as_disagreement():
    truth = {
        ("r", 1): {"repo": "r", "maintainer_type": "bug", "maintainer_types": ["bug"]},
        ("r", 2): {"repo": "r", "maintainer_type": "docs", "maintainer_types": ["docs", "test"]},
        ("s", 3): {"repo": "s", "maintainer_type": "test", "maintainer_types": ["test"]},
    }
    outputs = {
        ("r", 1): {"m": {"text": reply("bug"), "finish_reason": "stop"}},
        ("r", 2): {"m": {"text": reply("test"), "finish_reason": "stop"}},
        ("s", 3): {"m": {"text": "oops", "finish_reason": "length"}},
    }
    s = score_mode(truth, outputs, "m")
    assert s["agree_count"] == 1 and s["n"] == 3
    assert s["valid_json_first_attempt"] == 2
    assert s["agreement_with_any_maintainer_type"] == round(2 / 3, 4)
    assert s["confusion"]["test"] == {"INVALID": 1}
    assert s["by_repo"]["r"]["agreement"] == 0.5


def test_wilson_bounds():
    lo, hi = wilson(120, 200)
    assert 0.53 < lo < 0.6 < hi < 0.67
    assert wilson(0, 0) == [0.0, 0.0]
