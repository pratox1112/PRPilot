import json

import httpx

from prpilot_data import diffs
from prpilot_data.diffs import (
    BaseIndex,
    choose_head,
    diff_row,
    fetch_open_time_diff,
    first_parent_chain,
    load_archive,
    load_done,
    process_repo,
    summarize,
)


def commit(oid, date, parent=None):
    return {
        "oid": oid,
        "committedDate": date,
        "parents": {"nodes": [{"oid": parent}] if parent else []},
    }


def test_first_parent_chain_skips_commits_that_came_in_through_a_merge():
    # m2 merges branch commit b1 (dated before m1) into the branch
    nodes = [
        commit("m2", "2024-01-03T00:00:00Z", "m1"),
        commit("b1", "2024-01-01T12:00:00Z", "m0"),
        commit("m1", "2024-01-02T00:00:00Z", "m0"),
        commit("m0", "2024-01-01T00:00:00Z"),
    ]
    nodes[0]["parents"]["nodes"].append({"oid": "b1"})
    assert [oid for _, oid in first_parent_chain(nodes)] == ["m0", "m1", "m2"]


def test_base_index_picks_tip_at_open_and_tolerates_an_old_dated_push():
    chain = [
        ("2024-01-01T00:00:00Z", "a"),
        ("2024-01-03T00:00:00Z", "b"),
        ("2024-01-02T00:00:00Z", "c"),  # pushed later with an older date
        ("2024-01-05T00:00:00Z", "d"),
    ]
    index = BaseIndex(chain)
    assert index.at("2023-12-31T00:00:00Z") is None
    assert index.at("2024-01-02T12:00:00Z") == "a"
    assert index.at("2024-01-04T00:00:00Z") == "c"
    assert index.at("2024-01-06T00:00:00Z") == "d"


def pr_node(**over):
    node = {
        "repo": "o/r",
        "number": 7,
        "createdAt": "2024-01-04T00:00:00Z",
        "title": "t",
        "body": "",
        "baseRefName": "main",
        "headRefOid": "final",
        "userContentEdits": {"totalCount": 0, "nodes": []},
        "timelineItems": {"totalCount": 0, "nodes": []},
        "commits": {
            "totalCount": 2,
            "nodes": [
                {"commit": {"oid": "h1", "committedDate": "2024-01-03T23:00:00Z"}},
                {"commit": {"oid": "final", "committedDate": "2024-01-05T00:00:00Z"}},
            ],
        },
    }
    return {**node, **over}


def test_choose_head_prefers_archive_then_reconstruction_then_final():
    assert choose_head(pr_node(), {"head_sha": "gh"}) == ("gh", "gharchive")
    assert choose_head(pr_node(), None) == ("h1", "last_commit_before_open")
    late = pr_node(commits={"totalCount": 1, "nodes": [pr_node()["commits"]["nodes"][1]]})
    assert choose_head(late, None) == ("final", "final_head")


class FakeGH:
    def __init__(self, answers):
        self.answers = answers  # path -> text or exception
        self.paths = []

    def rest_get(self, path, accept):
        self.paths.append(path)
        answer = self.answers.get(path, httpx.HTTPStatusError("404", request=None, response=None))
        if isinstance(answer, Exception):
            raise answer
        return answer


INDEX = BaseIndex([("2024-01-01T00:00:00Z", "base0")])


def test_open_time_diff_uses_reconstructed_head_against_base_at_open():
    gh = FakeGH({"/repos/o/r/compare/base0...h1": "diff --git a/x b/x\n"})
    got = fetch_open_time_diff(gh, pr_node(), None, INDEX)
    assert got["diff_source"] == "open_time" and got["base_source"] == "first_parent_at_open"
    assert got["errors"] == []


def test_falls_back_to_pr_diff_never_final_head_against_old_base():
    gh = FakeGH(
        {
            "/repos/o/r/compare/base0...h1": "",  # empty compare is not accepted
            "/repos/o/r/pulls/7": "diff --git a/y b/y\n",
        }
    )
    got = fetch_open_time_diff(gh, pr_node(), None, INDEX)
    assert got["diff_source"] == "final_pr_diff" and got["errors"] == ["open_time: empty diff"]
    assert gh.paths == ["/repos/o/r/compare/base0...h1", "/repos/o/r/pulls/7"]


def test_unresolved_head_goes_straight_to_pr_diff():
    late = pr_node(commits={"totalCount": 1, "nodes": [pr_node()["commits"]["nodes"][1]]})
    gh = FakeGH({"/repos/o/r/pulls/7": "diff --git a/y b/y\n"})
    got = fetch_open_time_diff(gh, late, None, INDEX)
    assert (got["head_source"], got["diff_source"]) == ("final_head", "final_pr_diff")
    assert gh.paths == ["/repos/o/r/pulls/7"]


def test_archive_base_and_head_are_used_when_present():
    gh = FakeGH({"/repos/o/r/compare/B...H": "diff --git a/x b/x\n"})
    got = fetch_open_time_diff(gh, pr_node(), {"head_sha": "H", "base_sha": "B"}, INDEX)
    assert (got["head_source"], got["base_source"], got["diff_source"]) == (
        "gharchive",
        "gharchive",
        "open_time",
    )


def test_unavailable_when_everything_fails():
    got = fetch_open_time_diff(FakeGH({}), pr_node(), None, INDEX)
    assert got["diff_source"] == "unavailable" and got["diff"] == "" and len(got["errors"]) == 2


def test_diff_row_lists_files_from_full_diff_even_when_truncated(monkeypatch):
    monkeypatch.setattr(diffs, "DIFF_TOKENS", 40)
    full = "".join(f"diff --git a/f{i} b/f{i}\n@@ -1 +1 @@\n" + "+x\n" * 50 for i in range(3))
    gh = FakeGH({"/repos/o/r/compare/base0...h1": full})
    row = diff_row(gh, pr_node(), None, INDEX, lambda t: len(t.split()))
    assert row["changed_files"] == ["f0", "f1", "f2"] and row["truncated"]
    assert len(row["diff"].split()) <= 40 and row["diff_chars"] == len(full)


def test_load_done_drops_a_half_written_last_line(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text('{"number": 1}\n{"number": 2}\n{"numb', encoding="utf-8")
    assert load_done(path) == {1, 2}
    assert path.read_text(encoding="utf-8") == '{"number": 1}\n{"number": 2}\n'


def test_process_repo_resumes(tmp_path, monkeypatch):
    out = tmp_path / "o__r.jsonl"
    out.write_text(json.dumps({"number": 7}) + "\n", encoding="utf-8")
    fetched = []
    monkeypatch.setattr(
        diffs,
        "diff_row",
        lambda gh, node, *a: fetched.append(node["number"]) or {"number": node["number"]},
    )
    written = process_repo(None, [pr_node(), pr_node(number=8)], {}, INDEX, len, out)
    assert written == 1 and fetched == [8]


def test_load_archive_keeps_earliest_event(tmp_path):
    path = tmp_path / "a.jsonl"
    rows = [
        {"repo": "o/r", "number": 1, "head_sha": "late", "event_created_at": "2025-11-02"},
        {"repo": "o/r", "number": 1, "head_sha": "early", "event_created_at": "2025-11-01"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert load_archive(path)[("o/r", 1)]["head_sha"] == "early"


def test_summarize_counts_sources():
    rows = [
        {
            "diff_source": "open_time",
            "head_source": "gharchive",
            "base_source": "gharchive",
            "truncated": True,
            "diff_tokens": 9000,
        },
        {
            "diff_source": "final_pr_diff",
            "head_source": "final_head",
            "base_source": "first_parent_at_open",
            "truncated": False,
            "diff_tokens": None,
        },
    ]
    s = summarize(rows)
    assert s["prs"] == 2 and s["truncated"] == 1 and s["too_large_to_count"] == 1
    assert s["diff_source"] == {"open_time": 1, "final_pr_diff": 1}
