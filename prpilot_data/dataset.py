"""Stage E: one row per PR with open-time inputs, targets and flags, split by time (§4.4, §4.5).

- Inputs (only what existed when the PR was opened): the open-time title and body (body with
  slash-command lines removed: `/kind bug` names the type, `/cc @x` names a reviewer), author
  association, base branch, the file list and the diff cut to the student token budget.
  `author_association` is GitHub's current value (no open-time source for it); the data card
  says so.
- Targets: stage D (human labels, type, reviewers, issues) and risk = the PR was later
  reverted, from the Phase 0 revert scan (task 0.5: too few positives for a metric, so risk
  is reported, not scored).
- Flags: `open_time_diff` is false when stage C fell back to the final diff (unresolved head
  or failed compare); `diff_inflated` marks open-time diffs with far more files than the
  merged PR (FILES_FACTOR x + FILES_EXTRA), usually a head that contained merged-in branch
  changes. `flagged` is either one; metrics are reported for clean and flagged PRs.
- Splits by opening date; the held-out repo goes to test whole. A PR whose title and file set
  equal one in another split is a duplicate: the copies outside the newest split are dropped,
  so the test split is never reduced.
"""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from prpilot_data.inputs import strip_label_commands
from prpilot_data.open_time import reconstruct

SPLITS = ("train", "val", "test")  # oldest first
TRAIN_END = "2024-12-31"
VAL_END = "2025-06-30"
TEST_END = "2026-03-31"
FILES_FACTOR = 3
FILES_EXTRA = 20


def split_of(created_at: str, split_role: str) -> str:
    if split_role == "held_out":
        return "test"
    day = created_at[:10]
    if day <= TRAIN_END:
        return "train"
    if day <= VAL_END:
        return "val"
    if day <= TEST_END:
        return "test"
    raise ValueError(f"opened {day}, after the data range")


def load_reverts(path: Path) -> dict[tuple[str, int], dict]:
    """Reverted PR -> how it was found, from results/feasibility_reverts.json."""
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        (repo, o["number"]): {"reverted_by": o["reverted_by"], "link": o["link"]}
        for repo, r in report["repos"].items()
        for o in r["originals"]
    }


def is_inflated(diff_source: str, files: list[str], final_files: int) -> bool:
    return diff_source == "open_time" and len(files) > FILES_FACTOR * final_files + FILES_EXTRA


def build_row(node: dict, diff: dict, truth: dict, revert: dict | None, split: str) -> dict:
    snapshot = reconstruct(node)
    files = diff["changed_files"]
    inflated = is_inflated(diff["diff_source"], files, node["files"]["totalCount"])
    return {
        "repo": node["repo"],
        "number": node["number"],
        "split": split,
        "created_at": node["createdAt"],
        "merged_at": node.get("mergedAt"),
        "author": truth["author"],
        # inputs
        "title": snapshot["title"],
        "body": strip_label_commands(snapshot["body"]),
        "author_association": node.get("authorAssociation"),
        "base_branch": node["baseRefName"],
        "changed_files": files,
        "diff": diff["diff"],
        "diff_truncated": diff["truncated"],
        "diff_tokens": diff["diff_tokens"],
        # provenance and flags
        "head_sha": diff["head_sha"],
        "head_source": diff["head_source"],
        "base_sha": diff["base_sha"],
        "base_source": diff["base_source"],
        "diff_source": diff["diff_source"],
        "open_time_diff": diff["diff_source"] == "open_time",
        "diff_inflated": inflated,
        "flagged": diff["diff_source"] != "open_time" or inflated,
        "author_is_bot": truth["author_is_bot"],
        # targets
        "labels": truth["labels"],
        "primary_type": truth["primary_type"],
        "types": truth["types"],
        "label_attribution": [{"label": k, **v} for k, v in truth["label_attribution"].items()],
        "reviewers": truth["reviewers"],
        "reviewer_evidence": [
            {"login": k, "evidence": v} for k, v in truth["reviewer_evidence"].items()
        ],
        "excluded_requests": [
            {"reason": k, "count": v} for k, v in sorted(truth["excluded_requests"].items())
        ],
        "issues": truth["issues"],
        "reverted": revert is not None,
        "reverted_by": (revert or {}).get("reverted_by"),
        "revert_link": (revert or {}).get("link"),
    }


def duplicate_key(row: dict) -> tuple | None:
    """Same title (case and spacing ignored) and the same set of files; None without files."""
    if not row["changed_files"]:
        return None
    return (" ".join(row["title"].casefold().split()), tuple(sorted(row["changed_files"])))


def drop_cross_split_duplicates(rows: list[dict]) -> tuple[list[dict], dict]:
    """Keep each duplicate group only in its newest split."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if (key := duplicate_key(r)) is not None:
            groups[key].append(r)
    dropped: set[tuple[str, int]] = set()
    by_split: Counter = Counter()
    cross = within = 0
    examples = []
    for members in groups.values():
        if len(members) < 2:
            continue
        splits = {m["split"] for m in members}
        if len(splits) == 1:
            within += 1
            continue
        cross += 1
        newest = max(splits, key=SPLITS.index)
        for m in members:
            if m["split"] != newest:
                dropped.add((m["repo"], m["number"]))
                by_split[m["split"]] += 1
        if len(examples) < 10:
            examples.append(sorted(f"{m['repo']}#{m['number']} ({m['split']})" for m in members))
    kept = [r for r in rows if (r["repo"], r["number"]) not in dropped]
    report = {
        "cross_split_groups": cross,
        "dropped_by_split": dict(by_split),
        "within_split_groups_kept": within,
        "examples": examples,
    }
    return kept, report


def check_no_leakage(rows: list[dict]) -> None:
    """No PR in two splits, and no duplicate group across splits (§4.5)."""
    seen: dict[tuple[str, int], str] = {}
    for r in rows:
        k = (r["repo"], r["number"])
        if k in seen:
            raise ValueError(f"{k} appears twice ({seen[k]}, {r['split']})")
        seen[k] = r["split"]
    splits_of: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if (key := duplicate_key(r)) is not None:
            splits_of[key].add(r["split"])
    crossing = [k for k, s in splits_of.items() if len(s) > 1]
    if crossing:
        raise ValueError(f"{len(crossing)} duplicate groups span splits, e.g. {crossing[0][0]!r}")


def summarize(rows: list[dict]) -> dict:
    n = len(rows)

    def share(k: int) -> float | None:
        return round(k / n, 4) if n else None

    flagged = sum(r["flagged"] for r in rows)
    return {
        "prs": n,
        "by_repo": dict(sorted(Counter(r["repo"] for r in rows).items())),
        "flagged": flagged,
        "flagged_share": share(flagged),
        "diff_source": dict(Counter(r["diff_source"] for r in rows)),
        "diff_inflated": sum(r["diff_inflated"] for r in rows),
        "diff_truncated": sum(r["diff_truncated"] for r in rows),
        "bot_authored": sum(r["author_is_bot"] for r in rows),
        "with_type": sum(r["primary_type"] is not None for r in rows),
        "primary_type": dict(Counter(r["primary_type"] or "none" for r in rows).most_common()),
        "with_reviewer": sum(bool(r["reviewers"]) for r in rows),
        "with_reviewed_reviewer": sum(
            any("reviewed" in e["evidence"] for e in r["reviewer_evidence"]) for r in rows
        ),
        "with_issue": sum(bool(r["issues"]) for r in rows),
        "reverted": sum(r["reverted"] for r in rows),
        "reverted_rate": share(sum(r["reverted"] for r in rows)),
    }


def schema():
    import pyarrow as pa

    s, i, b = pa.string(), pa.int64(), pa.bool_()
    return pa.schema(
        [
            ("repo", s),
            ("number", i),
            ("split", s),
            ("created_at", s),
            ("merged_at", s),
            ("author", s),
            ("title", s),
            ("body", s),
            ("author_association", s),
            ("base_branch", s),
            ("changed_files", pa.list_(s)),
            ("diff", s),
            ("diff_truncated", b),
            ("diff_tokens", i),
            ("head_sha", s),
            ("head_source", s),
            ("base_sha", s),
            ("base_source", s),
            ("diff_source", s),
            ("open_time_diff", b),
            ("diff_inflated", b),
            ("flagged", b),
            ("author_is_bot", b),
            ("labels", pa.list_(s)),
            ("primary_type", s),
            ("types", pa.list_(s)),
            (
                "label_attribution",
                pa.list_(
                    pa.struct(
                        [
                            ("label", s),
                            ("kind", s),
                            ("applied_by", s),
                            ("applied_at", s),
                            ("after_merge", b),
                        ]
                    )
                ),
            ),
            ("reviewers", pa.list_(s)),
            ("reviewer_evidence", pa.list_(pa.struct([("login", s), ("evidence", pa.list_(s))]))),
            ("excluded_requests", pa.list_(pa.struct([("reason", s), ("count", i)]))),
            (
                "issues",
                pa.list_(
                    pa.struct(
                        [("repo", s), ("number", i), ("same_repo", b), ("mentioned_at_open", b)]
                    )
                ),
            ),
            ("reverted", b),
            ("reverted_by", i),
            ("revert_link", s),
        ]
    )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def write_parquet(rows: list[dict], path: Path) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema()), path, compression="zstd")
    return file_sha256(path)
