from datetime import date

from prpilot_data.discover import (
    Window,
    discover_repo,
    plan_windows,
    search_q,
    weekly_windows,
)


def test_weekly_windows_cover_range_without_gaps():
    ws = weekly_windows(date(2024, 1, 1), date(2024, 1, 20))
    assert ws[0] == Window(date(2024, 1, 1), date(2024, 1, 7))
    assert ws[-1].end == date(2024, 1, 20)
    for a, b in zip(ws, ws[1:], strict=False):
        assert (b.start - a.end).days == 1


def test_split_halves_inclusively():
    a, b = Window(date(2024, 1, 1), date(2024, 1, 7)).split()
    assert a == Window(date(2024, 1, 1), date(2024, 1, 4))
    assert b == Window(date(2024, 1, 5), date(2024, 1, 7))


def test_search_q_filters_merged_default_branch_and_created():
    q = search_q("o/r", "main", Window(date(2024, 1, 1), date(2024, 1, 7)))
    assert q == "repo:o/r is:pr is:merged base:main created:2024-01-01..2024-01-07"


class FakeGH:
    """Search counts by window length; 2 PRs per day, one created outside the range."""

    def __init__(self, per_day=2, limit_days=None):
        self.per_day = per_day

    def query(self, query, variables):
        if "defaultBranchRef" in query:
            return {"repository": {"defaultBranchRef": {"name": "main"}}}
        q = variables["q"]
        span = q.split("created:")[1]
        start, end = (date.fromisoformat(x) for x in span.split(".."))
        days = (end - start).days + 1
        nodes = []
        for d in range(days):
            day = date.fromordinal(start.toordinal() + d)
            for i in range(self.per_day):
                nodes.append(
                    {
                        "number": day.toordinal() * 10 + i,
                        "createdAt": f"{day}T10:00:00Z",
                        "mergedAt": f"{day}T12:00:00Z",
                        "baseRefName": "main",
                        "isDraft": False,
                        "author": {"__typename": "User", "login": "u"},
                    }
                )
        return {
            "search": {
                "issueCount": days * self.per_day,
                "nodes": nodes,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }


def test_plan_windows_splits_until_under_limit():
    gh = FakeGH(per_day=300)  # a week = 2,100 > 1,000
    ws = plan_windows(gh, "o/r", "main", [Window(date(2024, 1, 1), date(2024, 1, 7))])
    assert all(w.days * 300 <= 1000 for w in ws)
    assert ws[0].start == date(2024, 1, 1) and ws[-1].end == date(2024, 1, 7)


def test_discover_repo_counts_and_dedupes():
    rows, stats = discover_repo(FakeGH(), "o/r", date(2024, 1, 1), date(2024, 1, 10))
    assert stats["fetched_unique"] == len(rows) == 20
    assert stats["search_reported"] == 20 and stats["default_branch"] == "main"
    assert stats["by_month"] == {"2024-01": 20}
