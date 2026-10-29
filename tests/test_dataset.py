import json

import pytest

from prpilot_data.dataset import (
    build_row,
    check_no_leakage,
    drop_cross_split_duplicates,
    is_inflated,
    load_reverts,
    split_of,
    summarize,
    write_parquet,
)


def node(**over):
    base = {
        "repo": "o/r",
        "number": 9,
        "title": "Fix crash (renamed later)",
        "body": "Final body",
        "createdAt": "2025-03-01T00:00:00Z",
        "mergedAt": "2025-03-05T00:00:00Z",
        "baseRefName": "main",
        "authorAssociation": "CONTRIBUTOR",
        "files": {"totalCount": 2, "nodes": []},
        "userContentEdits": {
            "totalCount": 1,
            "nodes": [
                {"createdAt": "2025-03-02T00:00:00Z", "diff": "Fixes #4\n/kind bug\n/cc @bob"}
            ],
        },
        "timelineItems": {
            "totalCount": 1,
            "nodes": [
                {
                    "__typename": "RenamedTitleEvent",
                    "createdAt": "2025-03-03T00:00:00Z",
                    "previousTitle": "Fix crash",
                    "currentTitle": "Fix crash (renamed later)",
                }
            ],
        },
        "commits": {"totalCount": 0, "nodes": []},
    }
    return {**base, **over}


def diff(**over):
    base = {
        "head_sha": "h",
        "head_source": "last_commit_before_open",
        "base_sha": "b",
        "base_source": "first_parent_at_open",
        "diff_source": "open_time",
        "changed_files": ["a.py", "b.py"],
        "truncated": False,
        "diff_tokens": 120,
        "diff": "diff --git a/a.py b/a.py\n",
    }
    return {**base, **over}


TRUTH = {
    "author": "alice",
    "author_is_bot": False,
    "labels": ["kind/bug"],
    "primary_type": "bug",
    "types": ["bug"],
    "label_attribution": {
        "kind/bug": {
            "kind": "slash_command",
            "applied_by": "alice",
            "applied_at": "2025-03-01T00:01:00Z",
            "after_merge": False,
        }
    },
    "reviewers": ["bob"],
    "reviewer_evidence": {"bob": ["human_request", "reviewed"]},
    "excluded_requests": {"codeowners_at_open": 1},
    "issues": [{"repo": "o/r", "number": 4, "same_repo": True, "mentioned_at_open": True}],
}


def row(split="train", title="Fix crash", files=("a.py",), number=1, repo="o/r"):
    return {"repo": repo, "number": number, "split": split, "title": title, "changed_files": files}


def test_splits_by_opening_date_and_held_out_repo_goes_to_test():
    assert split_of("2024-12-31T23:59:59Z", "train_val_test") == "train"
    assert split_of("2025-01-01T00:00:00Z", "train_val_test") == "val"
    assert split_of("2025-07-01T00:00:00Z", "train_val_test") == "test"
    assert split_of("2023-02-01T00:00:00Z", "held_out") == "test"
    with pytest.raises(ValueError):
        split_of("2026-04-01T00:00:00Z", "train_val_test")


def test_row_uses_open_time_text_and_strips_slash_commands():
    r = build_row(node(), diff(), TRUTH, None, "val")
    assert r["title"] == "Fix crash"  # before the rename
    assert r["body"] == "Fixes #4"  # /kind (type) and /cc (reviewer) removed; issue ref kept
    assert r["reviewer_evidence"] == [{"login": "bob", "evidence": ["human_request", "reviewed"]}]
    assert r["label_attribution"][0]["label"] == "kind/bug"
    assert not r["flagged"] and not r["reverted"] and r["reverted_by"] is None


def test_final_diff_fallback_and_inflated_diffs_are_flagged():
    assert build_row(node(), diff(diff_source="final_pr_diff"), TRUTH, None, "val")["flagged"]
    files = [f"f{i}" for i in range(27)]  # 27 > 3 x 2 + 20
    r = build_row(node(), diff(changed_files=files), TRUTH, None, "val")
    assert r["diff_inflated"] and r["flagged"]
    assert not is_inflated("open_time", files[:26], 2)
    assert not is_inflated("final_pr_diff", files, 2)  # the final diff is never "inflated"


def test_revert_target_comes_from_the_revert_scan(tmp_path):
    report = {"repos": {"o/r": {"originals": [{"number": 9, "reverted_by": 12, "link": "x"}]}}}
    p = tmp_path / "reverts.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    reverts = load_reverts(p)
    r = build_row(node(), diff(), TRUTH, reverts.get(("o/r", 9)), "val")
    assert r["reverted"] and r["reverted_by"] == 12


def test_duplicates_across_splits_keep_only_the_newest_copy():
    rows = [
        row("train", "Fix  CRASH", ("b.py", "a.py"), 1),
        row("test", "fix crash", ("a.py", "b.py"), 2),
        row("train", "Bump x", ("req.txt",), 3),
        row("train", "Bump x", ("req.txt",), 4),  # same split: kept
        row("val", "Bump x", (), 5),  # no files: never a duplicate
    ]
    kept, report = drop_cross_split_duplicates(rows)
    assert [r["number"] for r in kept] == [2, 3, 4, 5]
    assert report["cross_split_groups"] == 1 and report["dropped_by_split"] == {"train": 1}
    assert report["within_split_groups_kept"] == 1
    check_no_leakage(kept)


def test_leakage_check_catches_repeats_and_cross_split_duplicates():
    with pytest.raises(ValueError, match="twice"):
        check_no_leakage([row("train"), row("test")])
    with pytest.raises(ValueError, match="span splits"):
        check_no_leakage([row("train", number=1), row("val", number=2)])


def test_summary_and_parquet_round_trip(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    rows = [
        build_row(node(), diff(), TRUTH, None, "val"),
        build_row(
            node(number=10),
            diff(diff_source="unavailable", changed_files=[], diff_tokens=None, diff=""),
            {**TRUTH, "primary_type": None, "types": [], "issues": [], "excluded_requests": {}},
            {"reverted_by": 12, "link": "commit_sha"},
            "val",
        ),
    ]
    s = summarize(rows)
    assert s["prs"] == 2 and s["flagged"] == 1 and s["reverted"] == 1 and s["with_type"] == 1
    path = tmp_path / "val.parquet"
    digest = write_parquet(rows, path)
    assert digest.startswith("sha256:") and write_parquet(rows, path) == digest  # deterministic
    back = pq.read_table(path).to_pylist()
    assert back[0]["issues"] == TRUTH["issues"] and back[1]["primary_type"] is None
