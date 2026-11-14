"""Targets files: the maintainer answers per PR, in the shape the eval harness scores (Phase 2).

The eval harness never reads the dataset or imports app code; it reads a prompt pack, a
targets file and model replies. One line per PR:
- `primary_type`: maintainer match target (None: PR has no type, not scored for type);
- `labels`: human labels added before merge that were in the PR's label list (after-merge
  labels cannot be predicted at opening); `labels_outside_list` counts the rest;
- `reviewers` / `reviewed`: all target reviewers / those who actually submitted a review;
- `issues` / `issues_named`: same-repo closing issues / those the open-time text named;
- `reverted`, `flagged`, `repo` for the risk metric and the breakdowns.
"""


def target_row(row: dict, label_list: list[str]) -> dict:
    allowed = set(label_list)
    before_merge = sorted(
        {
            a["label"]
            for a in row["label_attribution"]
            if a["label"] in row["labels"] and not a["after_merge"]
        }
    )
    same_repo = [i for i in row["issues"] if i["same_repo"]]
    return {
        "repo": row["repo"],
        "number": row["number"],
        "split": row["split"],
        "primary_type": row["primary_type"],
        "labels": [label for label in before_merge if label in allowed],
        "labels_outside_list": sum(label not in allowed for label in before_merge),
        "reviewers": row["reviewers"],
        "reviewed": sorted(
            e["login"] for e in row["reviewer_evidence"] if "reviewed" in e["evidence"]
        ),
        "issues": sorted(i["number"] for i in same_repo),
        "issues_named": sorted(i["number"] for i in same_repo if i["mentioned_at_open"]),
        "reverted": row["reverted"],
        "flagged": row["flagged"],
        "author_is_bot": row["author_is_bot"],
    }
