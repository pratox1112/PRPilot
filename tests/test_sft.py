import json

from prpilot_data.sft import example

RULES = {
    "pr_author": "alice",
    "labels": ["area/ci", "kind/bug", "kind/feature"],
    "eligible_reviewers": ["dave", "bob", "carol", "erin"],
    "open_issues": [30, 12, 7],
}
PACK = {
    "repo": "o/r",
    "number": 1,
    "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
    "rules": RULES,
}


def target(**over):
    base = {
        "primary_type": "bug",
        "labels": ["kind/bug"],
        "reviewers": ["carol", "bob", "zed", "dave", "erin"],
        "issues": [7, 12, 99],
    }
    return {**base, **over}


def teacher(**over):
    base = {
        "summary": "Fixes a crash.",
        "type": "bug",
        "risk": "medium",
        "labels": ["kind/feature"],
        "reviewers": ["erin"],
        "related_issues": [30],
        "rationale": "The feature label kind/feature fits.",
    }
    return "<think>hmm</think>" + json.dumps({**base, **over})


def test_kept_example_takes_teacher_prose_and_maintainer_choices():
    ex, reason = example(PACK, target(), teacher())
    assert reason == "kept"
    assert ex["messages"][:2] == PACK["messages"]
    answer = json.loads(ex["messages"][2]["content"])
    assert answer["summary"] == "Fixes a crash." and answer["risk"] == "medium"
    assert answer["labels"] == ["kind/bug"]
    # true reviewers in candidate order, unknown `zed` dropped, at most 3
    assert answer["reviewers"] == ["dave", "bob", "carol"]
    # true issues in candidate order, issue 99 (not offered) dropped
    assert answer["related_issues"] == [12, 7]
    assert ex["rationale_mentions_replaced"] is True


def test_rationale_check_ignores_names_that_are_only_substrings():
    ex, _ = example(PACK, target(), teacher(rationale="Looks like a kind/feature-flag fix."))
    assert ex["rationale_mentions_replaced"] is False


def test_drop_reasons():
    assert example(PACK, target(primary_type=None), teacher()) == (None, "no_type")
    assert example(PACK, target(), None) == (None, "no_reply")
    assert example(PACK, target(), "not json") == (None, "invalid")
    assert example(PACK, target(), teacher(type="docs")) == (None, "disagree")


def test_fill_puts_truth_first_then_teacher_picks():
    ex, _ = example(
        PACK,
        target(reviewers=["bob", "zed"], issues=[12]),
        teacher(reviewers=["bob", "mallory", "erin"], related_issues=[30, 12, 99]),
        fill=True,
    )
    answer = json.loads(ex["messages"][2]["content"])
    # truth (bob), then the teacher's eligible picks without repeats; mallory not eligible
    assert answer["reviewers"] == ["bob", "erin"]
    # truth (12), then teacher's open issues; 99 not offered
    assert answer["related_issues"] == [12, 30]


def test_fill_with_no_truth_keeps_teacher_picks_and_default_leaves_them_empty():
    tgt = target(reviewers=["zed"], issues=[99])
    filled, _ = example(PACK, tgt, teacher(), fill=True)
    plain, _ = example(PACK, tgt, teacher())
    assert json.loads(filled["messages"][2]["content"])["reviewers"] == ["erin"]
    assert json.loads(plain["messages"][2]["content"])["reviewers"] == []
