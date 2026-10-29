from prpilot_data.spot_check import render, report, sample


def row(repo, number, split="train", **over):
    base = {
        "repo": repo,
        "number": number,
        "split": split,
        "created_at": "2024-01-01T00:00:00Z",
        "merged_at": "2024-01-02T00:00:00Z",
        "author": "alice",
        "author_is_bot": False,
        "author_association": "MEMBER",
        "title": "Fix it",
        "body": "Fixes #4",
        "changed_files": ["a.py"],
        "diff_source": "open_time",
        "head_source": "last_commit_before_open",
        "flagged": False,
        "primary_type": "bug",
        "types": ["bug"],
        "labels": ["kind/bug"],
        "label_attribution": [
            {
                "label": "kind/bug",
                "kind": "slash_command",
                "applied_by": "alice",
                "applied_at": "2024-01-01T00:01:00Z",
                "after_merge": False,
            },
            {
                "label": "size/S",
                "kind": "bot",
                "applied_by": "k8s-ci-robot",
                "applied_at": "2024-01-01T00:00:30Z",
                "after_merge": False,
            },
        ],
        "reviewer_evidence": [{"login": "bob", "evidence": ["reviewed"]}],
        "excluded_requests": [{"reason": "codeowners_at_open", "count": 2}],
        "issues": [{"repo": repo, "number": 4, "same_repo": True, "mentioned_at_open": True}],
        "reverted": False,
        "reverted_by": None,
    }
    return {**base, **over}


def test_sample_is_seeded_balanced_and_never_touches_test():
    rows = [row("a/x", i) for i in range(50)] + [row("b/y", i) for i in range(50)]
    rows += [row("c/z", i, split="test") for i in range(50)]
    picked = sample(rows, 10, seed=0)
    assert picked == sample(rows, 10, seed=0)
    assert sum(r["repo"] == "a/x" for r in picked) == 5 and {r["split"] for r in picked} == {
        "train"
    }


def test_render_marks_targets_and_ignored_labels():
    text = render(row("o/r", 7), 1)
    assert "https://github.com/o/r/pull/7" in text
    assert "`kind/bug`: slash_command by alice" in text and "-> TARGET" in text
    assert "`size/S`: bot by k8s-ci-robot" in text and "-> ignored" in text
    assert "codeowners_at_open 2" in text and "o/r#4 (named at open)" in text


def test_report_lists_every_pr():
    assert report([row("o/r", 1), row("o/r", 2)], 0).count("## ") == 2
