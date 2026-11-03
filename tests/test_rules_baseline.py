import json

import pytest

from prpilot_data.rules_baseline import (
    history_predictions,
    reply,
    type_from_files,
    type_from_title,
)


def pr(number, opened, merged, files, labels=(), primary_type=None):
    return {
        "repo": "o/r",
        "number": number,
        "created_at": opened,
        "merged_at": merged,
        "changed_files": list(files),
        "primary_type": primary_type,
        "labels": list(labels),
        "label_attribution": [{"label": x, "after_merge": False} for x in labels],
        "title": "Something",
    }


@pytest.mark.parametrize(
    "files,expected",
    [
        (["docs/a.rst", "README.md"], "docs"),
        (["pkg/a_test.go", "tests/test_x.py"], "test"),
        ([".github/workflows/ci.yml"], "ci/build"),
        (["go.mod", "go.sum"], "dependency"),
        (["pkg/a.go", "docs/a.md"], None),
        ([], None),
    ],
)
def test_type_from_files(files, expected):
    assert type_from_files(files) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("BUG: fix crash in where", "bug"),
        ("DOC: fix typo", "docs"),
        ("ENH: add axis argument", "feature"),
        ("MAINT: remove unused code", "refactor"),
        ("TST: mark flaky", "test"),
        ("BLD: meson tweak", "ci/build"),
        ("chore(deps): bump golang.org/x/net", "dependency"),
        ("Something else entirely", None),
    ],
)
def test_type_from_title(title, expected):
    assert type_from_title(title) == expected


def test_history_uses_only_prs_merged_before_opening():
    early = [
        pr(i, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", ["pkg/net/a.go"], ["area/net"], "bug")
        for i in range(20)
    ]
    late = pr(99, "2024-01-10T00:00:00Z", "2024-03-01T00:00:00Z", ["pkg/net/b.go"], ["x"], "docs")
    target = pr(50, "2024-02-01T00:00:00Z", None, ["pkg/net/c.go"])
    hist = history_predictions([*early, late, target])
    assert hist[("o/r", 50)] == {"default_type": "bug", "labels": ["area/net"]}
    assert hist[("o/r", 0)] == {"default_type": "other", "labels": []}  # nothing merged yet


def test_reply_is_valid_reply_json_within_the_lists():
    row = {**pr(1, "2024-01-01T00:00:00Z", None, ["pkg/a.go"]), "title": "Fix crash"}
    cand = {
        "labels": ["area/net"],
        "reviewers": [{"login": x, "score": 1} for x in ("a", "b", "c", "d")],
        "issues": [{"number": n} for n in range(1, 8)],
    }
    out = reply(row, cand, {"default_type": "docs", "labels": ["area/net", "gone"]})
    assert out["type"] == "bug" and out["labels"] == ["area/net"]
    assert out["reviewers"] == ["a", "b", "c"] and out["related_issues"] == [1, 2, 3, 4, 5]
    json.dumps(out)
