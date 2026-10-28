from datetime import timedelta

from prpilot_data.open_time import (
    body_at_open,
    head_at_open,
    normalize_text,
    reconstruct,
    title_at_open,
)

OPEN = "2025-03-01T12:00:00Z"


def commit(oid, at):
    return {"oid": oid, "committedDate": at}


def test_title_unchanged_and_renamed():
    assert title_at_open("Final", []) == "Final"
    renames = [
        {"createdAt": "2025-03-02T00:00:00Z", "previousTitle": "Second", "currentTitle": "Final"},
        {"createdAt": "2025-03-01T13:00:00Z", "previousTitle": "First", "currentTitle": "Second"},
    ]
    assert title_at_open("Final", renames) == "First"


def test_body_unedited_and_edited():
    assert body_at_open("now", []) == "now"
    assert body_at_open(None, []) == ""
    # API order: newest first; the original and the first edit share a timestamp.
    edits = [
        {"createdAt": "2025-03-03T00:00:00Z", "diff": "v3"},
        {"createdAt": "2025-03-02T00:00:00Z", "diff": "v2"},
        {"createdAt": "2025-03-02T00:00:00Z", "diff": "v1"},
    ]
    assert body_at_open("v3", edits) == "v1"


def test_normalize_text():
    assert normalize_text(" a\r\nb \n") == "a\nb"
    assert normalize_text(None) == ""


def test_head_without_force_push_takes_last_commit_before_open():
    commits = [
        commit("a", "2025-03-01T10:00:00Z"),
        commit("b", "2025-03-01T11:59:00Z"),
        commit("c", "2025-03-02T09:00:00Z"),  # review fix pushed after opening
    ]
    h = head_at_open(OPEN, commits, [])
    assert (h.sha, h.method) == ("b", "last_commit_before_open")


def test_head_with_force_push_uses_commit_before_it():
    pushes = [
        {"createdAt": "2025-03-05T00:00:00Z", "beforeCommit": commit("y", "2025-03-04T00:00:00Z")},
        {"createdAt": "2025-03-02T00:00:00Z", "beforeCommit": commit("x", "2025-03-01T11:00:00Z")},
    ]
    h = head_at_open(OPEN, [commit("z", "2025-03-05T00:00:00Z")], pushes)
    assert (h.sha, h.method) == ("x", "before_first_force_push")


def test_head_unresolved_when_commits_landed_before_first_force_push():
    pushes = [
        {"createdAt": "2025-03-05T00:00:00Z", "beforeCommit": commit("y", "2025-03-03T00:00:00Z")}
    ]
    h = head_at_open(OPEN, [commit("z", "2025-03-05T00:00:00Z")], pushes)
    assert (h.sha, h.method) == (None, "unresolved")


def test_head_unresolved_when_no_commit_before_open_and_tolerance_helps():
    commits = [commit("a", "2025-03-01T12:02:00Z")]  # committer clock 2 minutes ahead
    assert head_at_open(OPEN, commits, []).method == "unresolved"
    h = head_at_open(OPEN, commits, [], tolerance=timedelta(minutes=5))
    assert h.sha == "a"


def test_reconstruct_combines_fields_and_flags():
    pr = {
        "title": "Final",
        "body": "edited",
        "createdAt": OPEN,
        "userContentEdits": {"totalCount": 1, "nodes": [{"createdAt": OPEN, "diff": "orig"}]},
        "timelineItems": {
            "totalCount": 1,
            "nodes": [
                {
                    "__typename": "RenamedTitleEvent",
                    "createdAt": "2025-03-02T00:00:00Z",
                    "previousTitle": "Orig",
                    "currentTitle": "Final",
                }
            ],
        },
        "commits": {"totalCount": 1, "nodes": [{"commit": commit("a", "2025-03-01T11:00:00Z")}]},
    }
    out = reconstruct(pr)
    assert out["title"] == "Orig" and out["body"] == "orig" and out["head_sha"] == "a"
    assert out["flags"]["renamed"] and out["flags"]["body_edited"]
    assert not out["flags"]["force_pushed"]
