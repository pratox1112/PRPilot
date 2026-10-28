import json

from prpilot_data.timeline import batch_query, capped_counts, fetch_repo


def conn(total):
    return {"totalCount": total, "nodes": []}


def node(n, **over):
    base = {
        "number": n,
        "files": conn(1),
        "closingIssuesReferences": conn(0),
        "labelEvents": conn(2),
        "reviewRequests": conn(0),
        "reviews": conn(1),
        "comments": conn(3),
        "userContentEdits": conn(0),
        "timelineItems": conn(0),
        "commits": conn(1),
    }
    return {**base, **over}


def test_batch_query_has_all_sections_once_per_pr():
    q = batch_query([5, 6])
    assert q.count("pr5: pullRequest(number: 5)") == 1
    for section in (
        "labelEvents:",
        "reviewRequests:",
        "closingIssuesReferences",
        "comments(",
        "files(",
        "userContentEdits",
        "HEAD_REF_FORCE_PUSHED_EVENT",
    ):
        assert section in q


def test_capped_counts_flags_only_overfull():
    flags = capped_counts(node(1, comments=conn(51), files=conn(100)))
    assert flags["comments"] and not flags["files"] and not flags["labelEvents"]


class FakeGH:
    def __init__(self):
        self.calls = self.cache_hits = 0

    def query(self, query, variables):
        nums = [int(part.split(":")[0]) for part in query.split("  pr")[1:]]
        return {"repository": {f"pr{n}": (None if n == 13 else node(n)) for n in nums}}


def test_fetch_repo_writes_nodes_and_counts_missing(tmp_path):
    out = tmp_path / "t.jsonl"
    written, missing, failed = fetch_repo(FakeGH(), "o/r", list(range(1, 25)), out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert written == 23 and missing == 1 and failed == [] and len(rows) == 23
    assert rows[0]["repo"] == "o/r" and rows[0]["number"] == 1


class FlakyGH(FakeGH):
    """Rejects any batch containing PR 7; PR 7 also fails on its own."""

    def query(self, query, variables):
        from prpilot_data.github_api import GraphQLError

        if "pr7:" in query:
            raise GraphQLError("Something went wrong while executing your query")
        return super().query(query, variables)


def test_failed_batch_is_retried_per_pr(tmp_path):
    out = tmp_path / "t.jsonl"
    written, missing, failed = fetch_repo(FlakyGH(), "o/r", list(range(1, 11)), out)
    assert written == 9 and missing == 0
    assert [f["number"] for f in failed] == [7]
