from prpilot_core.agents.triage_format import SYSTEM_PROMPT, PRContext, build_user_prompt
from prpilot_core.deps import RepoRules
from prpilot_data.prompts import pack_row, rules_for

ROW = {
    "repo": "o/r",
    "number": 9,
    "split": "val",
    "title": "Fix crash",
    "body": "Fixes #12",
    "author": "alice",
    "base_branch": "main",
    "changed_files": ["a.py"],
    "diff": "diff --git a/a.py b/a.py\n",
}
CAND = {
    "labels": ["kind/bug", "area/x"],
    "reviewers": [{"login": "zed", "score": 9}, {"login": "amy", "score": 2}],
    "issues": [
        {"number": 12, "title": "Crash", "named": True, "score": None},
        {"number": 3, "title": "Old crash", "named": False, "score": 1.5},
    ],
}


def section(prompt: str, tag: str) -> list[str]:
    return prompt.split(f"<{tag}>\n", 1)[1].split(f"\n</{tag}>", 1)[0].split("\n")


def test_ranked_lists_keep_their_order_in_the_prompt():
    prompt = build_user_prompt(PRContext("t", "", "alice", "main", (), ""), rules_for(CAND))
    assert section(prompt, "repo_eligible_reviewers") == ["zed", "amy"]
    assert section(prompt, "repo_open_issues") == ["#12: Crash", "#3: Old crash"]
    assert section(prompt, "repo_labels") == ["area/x", "kind/bug"]


def test_unranked_lists_are_sorted():
    rules = RepoRules(frozenset(), frozenset({"zed", "amy"}), {12: "b", 3: "a"})
    prompt = build_user_prompt(PRContext("t", "", "alice", "main", (), ""), rules)
    assert section(prompt, "repo_eligible_reviewers") == ["amy", "zed"]
    assert section(prompt, "repo_open_issues") == ["#3: a", "#12: b"]


def test_pack_row_holds_messages_and_validator_rules():
    p = pack_row(ROW, CAND)
    assert p["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "<pr_title>\nFix crash\n</pr_title>" in p["messages"][1]["content"]
    assert p["rules"] == {
        "pr_author": "alice",
        "labels": ["area/x", "kind/bug"],
        "eligible_reviewers": ["zed", "amy"],
        "open_issues": [12, 3],
    }
    assert pack_row(ROW, CAND) == p  # deterministic


def test_long_bodies_and_file_lists_are_capped():
    from prpilot_core.agents.triage_format import BODY_CUT_MARK, MAX_BODY_CHARS, MAX_FILES_LISTED

    files = tuple(f"f{i}.py" for i in range(MAX_FILES_LISTED + 5))
    pr = PRContext("t", "x" * (MAX_BODY_CHARS + 50), "alice", "main", files, "")
    prompt = build_user_prompt(pr, rules_for(CAND))
    assert section(prompt, "pr_body") == ["x" * MAX_BODY_CHARS, BODY_CUT_MARK.strip()]
    listed = section(prompt, "pr_changed_files")
    assert len(listed) == MAX_FILES_LISTED + 1 and listed[-1] == "[... and 5 more files]"
