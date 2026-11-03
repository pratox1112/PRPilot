"""Metric definitions (roadmap §5.3), one place, so resume numbers map to code.

Every reply is scored as given: an invalid reply (bad JSON, schema violation, missing)
predicts nothing, so it counts as wrong for type and as empty for labels, reviewers and
issues. Repo rules (labels from the list, eligible reviewers, open issues) are *not*
enforced here; that is the system pass with retries. Predictions outside the lists simply
miss, as they would for a maintainer.
"""

from collections import Counter
from collections.abc import Sequence

from prpilot_eval.stats import auroc, bootstrap_ci

TYPES = ("bug", "feature", "docs", "refactor", "test", "ci/build", "dependency", "other")
RISK_SCORE = {"low": 0, "medium": 1, "high": 2}
REVIEWERS_K = 3
ISSUES_K = 5


def per_pr(target: dict, reply: dict | None) -> dict:
    """One PR's scored outcome. `reply` is the parsed reply, or None when invalid."""
    pred = reply or {}
    labels = set(pred.get("labels", []))
    truth_labels = set(target["labels"])
    reviewers = pred.get("reviewers", [])[:REVIEWERS_K]
    issues = pred.get("related_issues", [])[:ISSUES_K]
    return {
        "repo": target["repo"],
        "number": target["number"],
        "flagged": target["flagged"],
        "valid": reply is not None,
        "type_truth": target["primary_type"],
        "type_pred": pred.get("type"),
        "type_correct": target["primary_type"] is not None
        and pred.get("type") == target["primary_type"],
        "label_tp": len(labels & truth_labels),
        "label_fp": len(labels - truth_labels),
        "label_fn": len(truth_labels - labels),
        "reviewer_hit": bool(set(reviewers) & set(target["reviewers"]))
        if target["reviewers"]
        else None,
        "reviewed_hit": bool(set(reviewers) & set(target["reviewed"]))
        if target["reviewed"]
        else None,
        "issue_hit": bool(set(issues) & set(target["issues"])) if target["issues"] else None,
        "issues_named": bool(target["issues_named"]),
        "risk_score": RISK_SCORE.get(pred.get("risk"), 0),
        "reverted": target["reverted"],
    }


def _rate(rows: Sequence[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    return sum(vals) / len(vals) if vals else None


def _type_rows(rows: Sequence[dict]) -> list[dict]:
    return [r for r in rows if r["type_truth"] is not None]


def maintainer_match(rows: Sequence[dict]) -> float | None:
    typed = _type_rows(rows)
    return sum(r["type_correct"] for r in typed) / len(typed) if typed else None


def type_macro_f1(rows: Sequence[dict]) -> float | None:
    """Mean F1 over the types that occur in the targets (an invalid reply predicts no type)."""
    typed = _type_rows(rows)
    classes = sorted({r["type_truth"] for r in typed})
    if not classes:
        return None
    f1s = []
    for c in classes:
        tp = sum(r["type_pred"] == c and r["type_truth"] == c for r in typed)
        fp = sum(r["type_pred"] == c and r["type_truth"] != c for r in typed)
        fn = sum(r["type_pred"] != c and r["type_truth"] == c for r in typed)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s)


def label_micro_f1(rows: Sequence[dict]) -> float | None:
    tp = sum(r["label_tp"] for r in rows)
    fp = sum(r["label_fp"] for r in rows)
    fn = sum(r["label_fn"] for r in rows)
    return 2 * tp / (2 * tp + fp + fn) if (tp + fp + fn) else None


def _metric(rows: Sequence[dict], fn, n: int) -> dict:
    value = fn(rows)
    return {
        "value": round(value, 4) if value is not None else None,
        "n": n,
        "ci95": bootstrap_ci(rows, fn),
    }


def summarize(rows: Sequence[dict], with_ci: bool = True) -> dict:
    def reviewer(rs):
        return _rate(rs, "reviewer_hit")

    def reviewed(rs):
        return _rate(rs, "reviewed_hit")

    def issue(rs):
        return _rate(rs, "issue_hit")

    def validity(rs):
        return _rate(rs, "valid")

    named = [r for r in rows if r["issue_hit"] is not None and r["issues_named"]]
    unnamed = [r for r in rows if r["issue_hit"] is not None and not r["issues_named"]]
    parts = {
        "maintainer_match": (maintainer_match, len(_type_rows(rows)), rows),
        "type_macro_f1": (type_macro_f1, len(_type_rows(rows)), rows),
        "label_micro_f1": (label_micro_f1, len(rows), rows),
        "reviewer_recall_at_3": (reviewer, sum(r["reviewer_hit"] is not None for r in rows), rows),
        "reviewer_recall_at_3_reviewed_only": (
            reviewed,
            sum(r["reviewed_hit"] is not None for r in rows),
            rows,
        ),
        "issue_recall_at_5": (issue, sum(r["issue_hit"] is not None for r in rows), rows),
        "issue_recall_at_5_named_at_open": (issue, len(named), named),
        "issue_recall_at_5_not_named": (issue, len(unnamed), unnamed),
        "json_validity": (validity, len(rows), rows),
    }
    out = {}
    for name, (fn, n, subset) in parts.items():
        if with_ci:
            out[name] = _metric(subset, fn, n)
        else:
            value = fn(subset)
            out[name] = {"value": round(value, 4) if value is not None else None, "n": n}
    out["risk_auroc"] = {
        "value": auroc([r["risk_score"] for r in rows], [r["reverted"] for r in rows]),
        "n": len(rows),
        "positives": sum(r["reverted"] for r in rows),
        "note": "reported only: too few reverted PRs for a metric (task 0.5)",
    }
    return out


def per_type(rows: Sequence[dict]) -> dict:
    typed = _type_rows(rows)
    out = {}
    for c in TYPES:
        mine = [r for r in typed if r["type_truth"] == c]
        if mine:
            out[c] = {
                "n": len(mine),
                "recall": round(sum(r["type_correct"] for r in mine) / len(mine), 4),
                "predicted": sum(r["type_pred"] == c for r in typed),
            }
    return out


def breakdowns(rows: Sequence[dict]) -> dict:
    groups = {
        f"repo:{repo}": [r for r in rows if r["repo"] == repo]
        for repo in sorted({r["repo"] for r in rows})
    }
    groups["clean"] = [r for r in rows if not r["flagged"]]
    groups["flagged"] = [r for r in rows if r["flagged"]]
    return {name: summarize(g, with_ci=False) for name, g in groups.items()}


def invalid_reasons(errors: Sequence[str | None]) -> dict:
    return dict(Counter(e.split(":", 1)[0] for e in errors if e).most_common())
