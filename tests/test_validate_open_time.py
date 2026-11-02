import json

from prpilot_data import gharchive
from prpilot_data.validate_open_time import batch_query, compare, load_truth, summarize


def test_load_truth_dedupes_across_files(tmp_path):
    a = tmp_path / "sample_opened.jsonl"
    b = tmp_path / "validation_opened.jsonl"
    row = {
        "repo": "o/r",
        "number": 7,
        "title": "t",
        "head_sha": "x",
        "event_created_at": "2025-01-01",
    }
    a.write_text(json.dumps(row) + "\n", encoding="utf-8")
    b.write_text(
        json.dumps({**row, "title": "dup"}) + "\n" + json.dumps({**row, "number": None}) + "\n",
        encoding="utf-8",
    )
    truth = load_truth([a, b])
    assert list(truth) == [("o/r", 7)] and truth[("o/r", 7)]["title"] == "t"


def test_batch_query_aliases_each_pr():
    q = batch_query([3, 11])
    assert "pr3: pullRequest(number: 3)" in q and "pr11: pullRequest(number: 11)" in q
    assert "userContentEdits" in q and "HEAD_REF_FORCE_PUSHED_EVENT" in q


def test_compare_before_and_after_trim():
    rec = {"title": "Orig", "body": "a\nb", "head_sha": "h1"}
    final = {"title": "Final", "body": "changed", "headRefOid": "h9"}
    before = {"title": "Orig", "body": "a\r\nb ", "head_sha": "h1"}
    row = compare(before, rec, final)
    assert row["title"] and row["body"] and row["head_sha"]
    assert row["naive_title"] is False and row["naive_head_sha"] is False
    after = {"title": None, "body": None, "head_sha": "h1"}
    row = compare(after, rec, final)
    assert row["title"] is None and row["body"] is None and row["head_sha"] is True


def test_summarize_skips_missing_truth():
    rows = [
        {
            "title": True,
            "body": None,
            "head_sha": False,
            "naive_title": False,
            "naive_body": None,
            "naive_head_sha": False,
        },
        {
            "title": None,
            "body": None,
            "head_sha": True,
            "naive_title": None,
            "naive_body": None,
            "naive_head_sha": True,
        },
    ]
    s = summarize(rows)
    assert s["title"] == {"n": 1, "agree": 1, "rate": 1.0}
    assert s["body"]["n"] == 0 and s["body"]["rate"] is None
    assert s["head_sha"]["rate"] == 0.5


def test_day_and_month_sql():
    assert "'230301'" in gharchive.days_sql(gharchive.VALIDATION_DAYS)
    assert "BETWEEN '2510' AND '2603'" in gharchive.post_trim_sql()
