from prpilot_data.complete import complete_node, connection_query, needs_completion


def conn(n):
    return {"totalCount": n, "nodes": [{"i": i} for i in range(n)]}


def node(**over):
    base = {
        "repo": "o/r",
        "number": 5,
        "reviews": conn(3),
        "comments": conn(2),
        "labelEvents": conn(1),
        "reviewRequests": conn(0),
        "timelineItems": conn(0),
        "commits": conn(1),
        "files": conn(100),
    }
    return {**base, **over}


def test_needs_completion_ignores_files_and_partial_pages():
    assert needs_completion(node()) == []
    assert needs_completion(node(reviews=conn(50), labelEvents=conn(100))) == [
        "reviews",
        "labelEvents",
    ]


def test_connection_query_paginates_with_after():
    q = connection_query("labelEvents")
    assert "after: $after" in q and "LABELED_EVENT" in q and "endCursor" in q


class PagedGH:
    """Serves 130 reviews in two pages."""

    def __init__(self):
        self.seen_after = []

    def query(self, query, variables):
        self.seen_after.append(variables["after"])
        first = variables["after"] is None
        items = [{"i": i} for i in (range(100) if first else range(100, 130))]
        return {
            "repository": {
                "pullRequest": {
                    "conn": {
                        "pageInfo": {"hasNextPage": first, "endCursor": "c1" if first else None},
                        "nodes": items,
                    }
                }
            }
        }


def test_complete_node_replaces_full_connection_with_all_pages():
    gh = PagedGH()
    out, keys = complete_node(gh, node(reviews=conn(50)))
    assert keys == ["reviews"] and len(out["reviews"]["nodes"]) == 130
    assert gh.seen_after == [None, "c1"]
    assert out["completed_connections"] == ["reviews"]
    assert len(out["comments"]["nodes"]) == 2  # untouched


def test_completed_connections_are_not_refetched():
    n = node(reviews=conn(130))
    n["completed_connections"] = ["reviews"]
    assert needs_completion(n) == []
