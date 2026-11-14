from collections import Counter

from prpilot_data.candidates import (
    IssueIndex,
    candidate_row,
    ceilings,
    label_vocabulary,
    labels_at,
    named_issues,
    path_keys,
    rank_reviewers,
)


def pr(number, opened, merged, files, reviewers=(), author="alice", **over):
    base = {
        "repo": "o/r",
        "number": number,
        "split": "train",
        "created_at": opened,
        "merged_at": merged,
        "author": author,
        "title": "Fix kubelet crash",
        "body": "",
        "changed_files": list(files),
        "reviewers": list(reviewers),
        "labels": [],
        "label_attribution": [],
        "issues": [],
    }
    return {**base, **over}


def attribution(label, after_merge=False):
    return {"label": label, "after_merge": after_merge}


def issue(n, created, closed=None, title="kubelet crash on restart", body=""):
    return {"number": n, "title": title, "body": body, "created_at": created, "closed_at": closed}


# --- labels ------------------------------------------------------------------------------


def test_vocabulary_counts_human_labels_before_merge_in_the_early_period():
    rows = [
        pr(
            1,
            "2024-01-01T00:00:00Z",
            None,
            [],
            labels=["bug"],
            label_attribution=[attribution("bug")],
        ),
        pr(
            2,
            "2024-01-01T00:00:00Z",
            None,
            [],
            labels=["bp"],
            label_attribution=[attribution("bp", True)],
        ),
        pr(
            3,
            "2025-02-01T00:00:00Z",
            None,
            [],
            labels=["bug"],
            label_attribution=[attribution("bug")],
        ),
        pr(4, "2024-01-01T00:00:00Z", None, [], labels=[], label_attribution=[attribution("bot")]),
    ]
    assert label_vocabulary(rows)["o/r"] == Counter({"bug": 1})


def test_labels_must_exist_at_opening_and_be_used_enough():
    repo_labels = [
        {"name": "bug", "created_at": "2015-01-01T00:00:00Z"},
        {"name": "new", "created_at": "2025-06-01T00:00:00Z"},
        {"name": "rare", "created_at": "2015-01-01T00:00:00Z"},
    ]
    uses = Counter({"bug": 10, "new": 50, "rare": 9})
    assert labels_at("2025-01-01T00:00:00Z", repo_labels, uses) == ["bug"]
    assert labels_at("2025-07-01T00:00:00Z", repo_labels, uses) == ["bug", "new"]


# --- reviewers ---------------------------------------------------------------------------


def test_path_keys_weight_deeper_paths_more():
    keys = path_keys(["pkg/kubelet/cm/x.go"])
    assert keys == {"pkg/": 1, "pkg/kubelet/": 2, "pkg/kubelet/cm/": 3, "pkg/kubelet/cm/x.go": 4}


def test_reviewers_come_only_from_prs_merged_before_opening():
    rows = [
        pr(1, "2024-01-01T00:00:00Z", "2024-01-05T00:00:00Z", ["pkg/a/x.go"], ["bob"]),
        pr(2, "2024-01-02T00:00:00Z", "2024-01-20T00:00:00Z", ["pkg/a/x.go"], ["carol"]),
        pr(3, "2024-01-10T00:00:00Z", "2024-01-11T00:00:00Z", ["pkg/a/y.go"], author="bob"),
        pr(4, "2024-01-10T00:00:00Z", "2024-01-11T00:00:00Z", ["pkg/a/x.go"]),
    ]
    ranked = rank_reviewers(rows)
    assert ranked[("o/r", 1)] == []  # nothing merged before it
    assert [r["login"] for r in ranked[("o/r", 4)]] == ["bob"]  # carol's PR merges later
    assert ranked[("o/r", 3)] == []  # bob is the author
    assert ranked[("o/r", 4)][0]["score"] == 1 + 2 + 4  # pkg/, pkg/a/, the file


def test_deeper_overlap_outranks_shallow_overlap():
    rows = [
        pr(1, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", ["pkg/b/z.go"], ["shallow"]),
        pr(2, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", ["pkg/a/q.go"], ["deep"]),
        pr(3, "2024-02-01T00:00:00Z", None, ["pkg/a/x.go"]),
    ]
    assert [r["login"] for r in rank_reviewers(rows)[("o/r", 3)]] == ["deep", "shallow"]


# --- issues ------------------------------------------------------------------------------


def test_named_issues_forms_and_other_repo_urls_ignored():
    text = "Fixes #12, see https://github.com/o/r/issues/7 and https://github.com/x/y/issues/9 #12"
    assert named_issues(text, "o/r") == [12, 7]
    assert named_issues("colour #12ab34", "o/r") == []


def test_only_issues_open_at_opening_are_candidates():
    index = IssueIndex(
        [
            issue(1, "2023-01-01T00:00:00Z"),  # open
            issue(2, "2023-01-01T00:00:00Z", closed="2023-06-01T00:00:00Z"),  # closed before
            issue(3, "2024-06-01T00:00:00Z"),  # created after
            issue(4, "2023-01-01T00:00:00Z", closed="2024-03-01T00:00:00Z", title="unrelated"),
        ]
    )
    got = index.rank("kubelet crash", "2024-01-01T00:00:00Z", [])
    assert [i["number"] for i in got] == [1]  # 4 is open but shares no term


def test_named_open_issue_comes_first_and_closed_named_issue_is_dropped():
    index = IssueIndex(
        [
            issue(1, "2023-01-01T00:00:00Z"),
            issue(5, "2023-01-01T00:00:00Z", title="something else"),
            issue(6, "2023-01-01T00:00:00Z", closed="2023-02-01T00:00:00Z"),
        ]
    )
    got = index.rank("kubelet crash", "2024-01-01T00:00:00Z", [5, 6])
    assert [(i["number"], i["named"]) for i in got] == [(5, True), (1, False)]


def test_candidate_row_and_ceilings():
    index = IssueIndex([issue(1, "2023-01-01T00:00:00Z")])
    row = pr(
        9,
        "2024-01-01T00:00:00Z",
        None,
        ["pkg/a/x.go"],
        ["bob"],
        body="Fixes #1",
        labels=["bug"],
        label_attribution=[attribution("bug")],
        issues=[{"number": 1, "same_repo": True}],
    )
    c = candidate_row(row, ["bug"], [{"login": "bob", "score": 3}], index)
    assert c["issues"][0] == {
        "number": 1,
        "score": None,
        "named": True,
        "title": issue(1, "")["title"],
    }
    s = ceilings([row], {("o/r", 9): c})
    assert s["reviewer_in_top3"]["rate"] == 1.0 and s["issue_in_top5"]["rate"] == 1.0
    assert s["target_labels_in_vocabulary"] == {"hits": 1, "n": 1, "rate": 1.0}
