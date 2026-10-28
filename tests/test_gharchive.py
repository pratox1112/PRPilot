import pytest

from prpilot_data import gharchive


def test_sample_sql_filters_days_type_repos_and_action():
    sql = gharchive.sample_sql()
    assert "`githubarchive.day.*`" in sql
    for day in gharchive.SAMPLE_DAYS:
        assert f"'{day}'" in sql
    assert "type = 'PullRequestEvent'" in sql
    assert "IN UNNEST(@repos)" in sql
    assert "'$.action') = 'opened'" in sql


def test_full_sql_uses_month_range():
    sql = gharchive.full_sql()
    assert "`githubarchive.month.*`" in sql
    assert "BETWEEN '202301' AND '202603'" in sql


def test_estimate_units():
    e = gharchive.Estimate(2**40)
    assert e.gib == 1024
    assert e.usd() == pytest.approx(6.25)


def test_byte_cap_refuses_over_budget():
    with pytest.raises(SystemExit):
        gharchive.byte_cap(gharchive.Estimate(200 * 2**30), budget_gib=100)
    assert gharchive.byte_cap(gharchive.Estimate(100), budget_gib=1) == 110


def test_completeness_per_day():
    rows = [
        {
            "event_created_at": "2025-06-16 10:00:00",
            "title": "a",
            "body": "",
            "head_sha": "x",
            "base_sha": "y",
            "base_ref": "main",
            "author": "u",
            "pr_created_at": "t",
            "payload_chars": 10,
        },
        {
            "event_created_at": "2025-06-16 11:00:00",
            "title": None,
            "body": None,
            "head_sha": None,
            "base_sha": None,
            "base_ref": None,
            "author": None,
            "pr_created_at": None,
            "payload_chars": 2,
        },
    ]
    s = gharchive.completeness(rows)["2025-06-16"]
    assert s["events"] == 2 and s["title"] == 0.5 and s["body"] == 0.0
