"""Rules baseline (roadmap §5.2, baseline 1): what every model must beat. No model involved.

- type: file rules first (every changed file is docs / tests / CI-build), then title
  keywords (numpy and pandas prefix titles with BUG:, DOC:, ENH:, MAINT:, TST:, BLD:...),
  else the repo's most common type among PRs merged before this one opened;
- labels: for each path prefix the PR touches, how often earlier PRs touching it carried
  each label; labels at >= LABEL_RATE on a prefix with >= LABEL_SUPPORT earlier PRs;
- reviewers: the top 3 path-history candidates; issues: the top 5 issue candidates (named
  issues first, then keyword matches); risk: medium for PRs touching many files, else low.
History only counts PRs merged before the PR opened, the same rule as the candidate lists.
Output is ordinary reply JSON, scored by the eval harness like any model's.
"""

import json
import re
from collections import Counter, defaultdict

from prpilot_data.candidates import PATH_DEPTH, ts

LABEL_RATE = 0.5
LABEL_SUPPORT = 20
MANY_FILES = 20

_DOC_FILE = re.compile(r"(^|/)(docs?|documentation)/|\.(md|rst|txt)$|(^|/)readme", re.IGNORECASE)
_TEST_FILE = re.compile(r"(^|/)(tests?|testing|testdata)/|(^|/)test_[^/]*$|_test\.\w+$", re.I)
_CI_FILE = re.compile(
    r"^\.github/|^\.circleci/|^ci/|^build/|(^|/)(Makefile|Dockerfile|meson\.build|"
    r"setup\.(py|cfg)|pyproject\.toml|\.gitlab-ci\.yml|azure-pipelines\.yml)$",
    re.IGNORECASE,
)
_DEP_FILE = re.compile(
    r"(^|/)(go\.(mod|sum)|requirements[^/]*\.txt|environment\.ya?ml|package(-lock)?\.json)$"
    r"|^vendor/",
    re.IGNORECASE,
)
TITLE_RULES = [  # first match wins, on the lowercased title
    ("dependency", r"\b(bump|deps?|dependenc(y|ies)|renovate)\b"),
    ("docs", r"^\W*(doc|docs|documentation)\b|\bdocs?\b|\btypos?\b|\breadme\b"),
    ("test", r"^\W*(tst|test|tests)\b|\btests?\b|\bflak"),
    ("ci/build", r"^\W*(ci|bld|build)\b|\bci\b|\bworkflows?\b|\bgithub actions?\b"),
    ("bug", r"^\W*(bug|fix|bugfix)\b|\bfix(es|ed)?\b|\bbug\b|\bregression\b|\bcrash"),
    ("feature", r"^\W*(enh|feat|feature)\b|\badds?\b|\bsupport\b|\bimplement|\bintroduce"),
    ("refactor", r"^\W*(maint|refactor|cleanup)\b|\brefactor|\bclean ?up|\bremove|\brename"),
]


def type_from_files(files: list[str]) -> str | None:
    if not files:
        return None
    for name, pattern in (
        ("docs", _DOC_FILE),
        ("test", _TEST_FILE),
        ("dependency", _DEP_FILE),
        ("ci/build", _CI_FILE),
    ):
        if all(pattern.search(f) for f in files):
            return name
    return None


def type_from_title(title: str) -> str | None:
    t = title.lower()
    for name, pattern in TITLE_RULES:
        if re.search(pattern, t):
            return name
    return None


def prefixes(files: list[str]) -> set[str]:
    out = set()
    for f in files[:200]:
        parts = f.split("/")
        for d in range(1, min(len(parts), PATH_DEPTH + 1)):
            out.add("/".join(parts[:d]) + "/")
    return out


def label_targets(row: dict) -> set[str]:
    return {
        a["label"]
        for a in row["label_attribution"]
        if a["label"] in row["labels"] and not a["after_merge"]
    }


def history_predictions(rows: list[dict]) -> dict[tuple[str, int], dict]:
    """Per PR, from PRs merged before it opened: default type and path-based labels."""
    out = {}
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    for repo_rows in by_repo.values():
        events = [(ts(r["created_at"]), 1, r) for r in repo_rows]
        events += [(ts(r["merged_at"]), 0, r) for r in repo_rows if r["merged_at"]]
        events.sort(key=lambda e: (e[0], e[1], e[2]["number"]))
        types: Counter = Counter()
        prs_on: Counter = Counter()
        labels_on: dict[str, Counter] = defaultdict(Counter)
        for _, kind, r in events:
            keys = prefixes(r["changed_files"])
            if kind == 0:
                if r["primary_type"]:
                    types[r["primary_type"]] += 1
                for key in keys:
                    prs_on[key] += 1
                    labels_on[key].update(label_targets(r))
                continue
            rate: dict[str, float] = {}
            for key in keys:
                if prs_on[key] >= LABEL_SUPPORT:
                    for label, n in labels_on[key].items():
                        rate[label] = max(rate.get(label, 0.0), n / prs_on[key])
            out[(r["repo"], r["number"])] = {
                "default_type": types.most_common(1)[0][0] if types else "other",
                "labels": sorted(label for label, x in rate.items() if x >= LABEL_RATE),
            }
    return out


def reply(row: dict, cand: dict, hist: dict) -> dict:
    files = row["changed_files"]
    pr_type = type_from_files(files) or type_from_title(row["title"]) or hist["default_type"]
    allowed = set(cand["labels"])
    return {
        "summary": "Rules baseline: no summary.",
        "type": pr_type,
        "risk": "medium" if len(files) >= MANY_FILES else "low",
        "labels": [label for label in hist["labels"] if label in allowed],
        "reviewers": [r["login"] for r in cand["reviewers"][:3]],
        "related_issues": [i["number"] for i in cand["issues"][:5]],
        "rationale": "Keyword, path and history rules.",
    }


def reply_line(row: dict, cand: dict, hist: dict) -> dict:
    return {
        "repo": row["repo"],
        "number": row["number"],
        "text": json.dumps(reply(row, cand, hist)),
        "finish_reason": "rules",
    }
