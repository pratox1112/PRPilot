from prpilot_data.issues import BODY_CHARS, fetch_issues, fetch_labels, issue_row
from prpilot_data.records import read_jsonl


class FakeGH:
    """Two pages of issues, one label page."""

    def __init__(self):
        self.afters = []

    def query(self, query, variables):
        self.afters.append(variables["after"])
        if "labels(" in query:
            nodes = [{"name": "kind/bug", "createdAt": "2015-01-01T00:00:00Z"}]
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
            return {"repository": {"labels": page}}
        first = variables["after"] is None
        nodes = [issue(1), issue(2)] if first else [issue(3, closed="2024-01-01T00:00:00Z"), None]
        page = {
            "totalCount": 3,
            "pageInfo": {"hasNextPage": first, "endCursor": "c1" if first else None},
            "nodes": nodes,
        }
        return {"repository": {"issues": page}}


def issue(n, closed=None, body="text"):
    return {
        "number": n,
        "title": f"Issue {n}",
        "body": body,
        "createdAt": "2023-01-01T00:00:00Z",
        "closedAt": closed,
        "author": {"__typename": "User", "login": "u"},
    }


def test_fetch_issues_follows_pages_and_skips_null_nodes(tmp_path):
    gh = FakeGH()
    out = tmp_path / "issues.jsonl"
    assert fetch_issues(gh, "o/r", out) == (3, 3)
    rows = read_jsonl(out)
    assert [r["number"] for r in rows] == [1, 2, 3]
    assert rows[2]["closed_at"] == "2024-01-01T00:00:00Z" and rows[0]["closed_at"] is None
    assert gh.afters == [None, "c1"]


def test_trial_stops_after_max_pages(tmp_path):
    assert fetch_issues(FakeGH(), "o/r", tmp_path / "t.jsonl", max_pages=1) == (2, 3)


def test_issue_body_is_cut_and_missing_author_kept():
    row = issue_row({**issue(5, body="x" * (BODY_CHARS + 10)), "author": None})
    assert len(row["body"]) == BODY_CHARS and row["author"] is None


def test_labels_keep_creation_time():
    assert fetch_labels(FakeGH(), "o/r") == [
        {"name": "kind/bug", "created_at": "2015-01-01T00:00:00Z"}
    ]
