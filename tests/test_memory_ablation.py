import json

from prpilot_data.memory_ablation import apply_rule

RULES = {
    "pr_author": "carol",
    "labels": ["kind/bug"],
    "eligible_reviewers": ["dave", "erin", "fay"],
    "open_issues": [12],
}


def reply(**over):
    base = {"summary": "Fixes a crash.", "type": "bug", "risk": "low", "labels": ["kind/bug"],
            "reviewers": ["fay"], "related_issues": [], "rationale": "Small fix."}  # fmt: skip
    return {"repo": "o/r", "number": 1, "text": json.dumps({**base, **over}), "agent_valid": True}


def reviewers(r):
    return json.loads(r["text"])["reviewers"]


def test_rules_change_only_the_reviewers():
    r = reply()
    assert reviewers(apply_rule(r, RULES, "model")) == ["fay"]
    assert reviewers(apply_rule(r, RULES, "model_then_memory")) == ["fay", "dave", "erin"]
    assert reviewers(apply_rule(r, RULES, "memory_then_model")) == ["dave", "erin", "fay"]
    assert reviewers(apply_rule(r, RULES, "memory")) == ["dave", "erin", "fay"]
    out = json.loads(apply_rule(r, RULES, "memory")["text"])
    assert {k: v for k, v in out.items() if k != "reviewers"} == {
        k: v for k, v in json.loads(r["text"]).items() if k != "reviewers"
    }


def test_invalid_reply_is_filtered_like_the_pipeline_and_unparseable_kept():
    bad = reply(labels=["kind/bug", "nope"], reviewers=["carol", "fay"])  # author, unknown label
    out = json.loads(apply_rule(bad, RULES, "model")["text"])
    assert out["labels"] == ["kind/bug"] and out["reviewers"] == ["fay"]
    junk = {"repo": "o/r", "number": 1, "text": "not json", "agent_valid": False}
    assert apply_rule(junk, RULES, "memory") is junk
