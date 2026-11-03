import hashlib
import json

import pytest

from prpilot_eval import metrics
from prpilot_eval.__main__ import compare, score
from prpilot_eval.stats import auroc, bootstrap_ci, mcnemar_exact
from prpilot_eval.validate import check

GOOD = {
    "summary": "Fixes a crash.",
    "type": "bug",
    "risk": "low",
    "labels": ["kind/bug"],
    "reviewers": ["bob"],
    "related_issues": [12],
    "rationale": "The diff guards a nil pointer.",
}


def reply(**over):
    return json.dumps({**GOOD, **over})


def target(**over):
    base = {
        "repo": "o/r",
        "number": 1,
        "split": "val",
        "primary_type": "bug",
        "labels": ["kind/bug"],
        "labels_outside_list": 0,
        "reviewers": ["bob", "carol"],
        "reviewed": ["carol"],
        "issues": [12],
        "issues_named": [12],
        "reverted": False,
        "flagged": False,
        "author_is_bot": False,
    }
    return {**base, **over}


# --- validation --------------------------------------------------------------------------


def test_valid_reply_and_think_block():
    assert check(reply())[1] is None
    assert check("<think>hmm</think>\n" + reply())[0]["type"] == "bug"


@pytest.mark.parametrize(
    "text,kind",
    [
        ("```json\n" + reply() + "\n```", "json"),
        (reply(extra=1), "schema"),
        (reply(type="security"), "schema"),
        (reply(summary="One. Two. Three. Four."), "schema"),
        (reply(reviewers=["bob", "bob"]), "schema"),
        (reply(reviewers=["a", "b", "c", "d"]), "schema"),
        (reply(labels=["  "]), "schema"),
        (reply(related_issues=[12.0]), "schema"),
        (reply(related_issues=[True]), "schema"),
        (reply(summary="   "), "schema"),
        ("[]", "schema"),
    ],
)
def test_invalid_replies(text, kind):
    parsed, error = check(text)
    assert parsed is None and error.startswith(kind)


def test_labels_are_stripped_and_duplicates_after_stripping_rejected():
    assert check(reply(labels=[" kind/bug "]))[0]["labels"] == ["kind/bug"]
    assert check(reply(labels=["a", " a"]))[0] is None


# --- metrics -----------------------------------------------------------------------------


def test_invalid_reply_counts_as_wrong_and_empty():
    r = metrics.per_pr(target(), None)
    assert not r["valid"] and not r["type_correct"] and r["label_fn"] == 1
    assert r["reviewer_hit"] is False and r["issue_hit"] is False


def test_hits_use_the_first_k_and_reviewed_only_separately():
    parsed = check(reply(reviewers=["x", "y", "bob"], related_issues=[1, 2, 3, 4, 5, 12]))[0]
    r = metrics.per_pr(target(), parsed)
    assert r["reviewer_hit"] is True and r["reviewed_hit"] is False  # bob only requested
    assert r["issue_hit"] is False  # #12 is sixth


def test_untyped_prs_are_not_scored_for_type():
    rows = [
        metrics.per_pr(target(), check(reply())[0]),
        metrics.per_pr(target(number=2, primary_type=None), check(reply())[0]),
    ]
    s = metrics.summarize(rows, with_ci=False)
    assert s["maintainer_match"] == {"value": 1.0, "n": 1}


def test_macro_f1_and_micro_f1():
    rows = [
        metrics.per_pr(target(), check(reply())[0]),
        metrics.per_pr(target(number=2, primary_type="docs", labels=[]), check(reply())[0]),
    ]
    # bug: tp1 fp1 -> F1 2/3; docs: tp0 -> 0; mean 1/3
    assert metrics.type_macro_f1(rows) == pytest.approx(1 / 3)
    # labels: tp1, fp1 (second PR), fn0 -> 2/3
    assert metrics.label_micro_f1(rows) == pytest.approx(2 / 3)


def test_breakdowns_split_repo_and_flagged():
    rows = [
        metrics.per_pr(target(), check(reply())[0]),
        metrics.per_pr(target(number=2, repo="x/y", flagged=True), None),
    ]
    b = metrics.breakdowns(rows)
    assert b["repo:o/r"]["maintainer_match"]["value"] == 1.0
    assert b["flagged"]["json_validity"]["value"] == 0.0


# --- stats -------------------------------------------------------------------------------


def test_mcnemar_exact_known_value():
    a = [True] * 10 + [False] * 0
    b = [False] * 8 + [True] * 2
    out = mcnemar_exact(a, b)
    assert (out["a_only"], out["b_only"]) == (8, 0)
    assert out["p_value"] == pytest.approx(2 / 2**8, abs=1e-6)


def test_auroc_and_bootstrap():
    assert auroc([0, 1, 2], [False, False, True]) == 1.0
    assert auroc([0, 0], [False, True]) == 0.5
    assert auroc([0], [False]) is None
    ci = bootstrap_ci([1, 0, 1, 1], lambda rs: sum(rs) / len(rs))
    assert ci == bootstrap_ci([1, 0, 1, 1], lambda rs: sum(rs) / len(rs))  # seeded
    assert 0 <= ci[0] <= 0.75 <= ci[1] <= 1


# --- end to end --------------------------------------------------------------------------


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_score_refuses_replies_from_another_pack_and_scores_missing_as_invalid(tmp_path):
    pack = write(
        tmp_path / "pack.jsonl", [{"repo": "o/r", "number": 1}, {"repo": "o/r", "number": 2}]
    )
    targets = write(tmp_path / "targets.jsonl", [target(), target(number=2)])
    replies = write(
        tmp_path / "m.jsonl", [{"repo": "o/r", "number": 1, "text": reply(), "prompt_tokens": 10}]
    )
    meta = tmp_path / "m.meta.json"
    meta.write_text(json.dumps({"name": "m", "pack_sha256": "sha256:other"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="pack"):
        score(pack, targets, replies)
    pack_sha = "sha256:" + hashlib.sha256(pack.read_bytes()).hexdigest()
    meta.write_text(json.dumps({"name": "m", "pack_sha256": pack_sha}), encoding="utf-8")
    report, records = score(pack, targets, replies)
    assert report["metrics"]["json_validity"]["value"] == 0.5
    assert report["invalid_reasons"] == {"missing": 1}
    assert report["tokens_per_pr"]["prompt_tokens"] == 10.0
    rec = write(tmp_path / "a.records.jsonl", records)
    assert compare(rec, rec)["p_value"] == 1.0
