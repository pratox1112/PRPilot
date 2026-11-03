"""Open-time candidate lists per PR: what a triage model may choose from (Phase 2).

The prompt asks for labels, reviewers and related issues out of given lists, and the
repo-rule validator rejects anything else, so for historical PRs each list must hold only
what existed when the PR was opened (§4.4):

- Labels: repo labels created before the PR opened that people applied (before merge) at
  least LABEL_MIN_USES times on the repo's PRs opened before VOCAB_BEFORE. Bot-only and
  rare labels stay out. For the held-out repo that period is its own early PRs; only label
  names come from them, never a PR's answer.
- Reviewers: people who reviewed earlier PRs that touched the same paths, counting only PRs
  merged before this one opened. Score = sum over shared paths of path depth (the exact file
  counts most), so a reviewer of `pkg/kubelet/cm/` beats one of `pkg/`. Never the author.
- Issues: issues open when the PR opened (created before, not yet closed), ranked by BM25
  between the PR's open-time title, body and file paths and the issue's title and body.
  Open issues the PR text names (`#N`, URL) come first. BM25 term statistics come from
  issues created before VOCAB_BEFORE, so test-period issues do not shape the ranking.

The reviewer and issue rankings are also the rules baseline for those two fields.
"""

import math
import re
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

LABEL_MIN_USES = 10
VOCAB_BEFORE = "2025-01-01"
TOP_REVIEWERS = 20
TOP_ISSUES = 20
MAX_FILES = 200  # a vendoring PR touches thousands; the first 200 paths say enough
PATH_DEPTH = 3
BM25_K1, BM25_B = 1.2, 0.75

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "this",
        "that",
        "with",
        "it",
        "as",
        "by",
        "at",
        "from",
        "not",
        "we",
        "can",
        "if",
        "when",
        "should",
        "would",
        "will",
        "was",
        "were",
        "has",
        "have",
        "but",
        "which",
        "so",
        "do",
        "does",
        "no",
        "yes",
    ]
)
_ISSUE_REF = re.compile(
    r"(?:(?<![\w/.#-])#(?P<n>\d+)(?!\w))|(?:github\.com/(?P<repo>[\w.-]+/[\w.-]+)/issues/(?P<u>\d+))",
    re.IGNORECASE,
)


def ts(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


# --- labels ------------------------------------------------------------------------------


def label_vocabulary(rows: list[dict]) -> dict[str, Counter]:
    """Per repo: how often people applied each label before merge, on PRs opened before
    VOCAB_BEFORE."""
    uses: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["created_at"] >= VOCAB_BEFORE:
            continue
        for a in r["label_attribution"]:
            if a["label"] in r["labels"] and not a["after_merge"]:
                uses[r["repo"]][a["label"]] += 1
    return uses


def labels_at(opened: str, repo_labels: list[dict], uses: Counter) -> list[str]:
    return sorted(
        label["name"]
        for label in repo_labels
        if label["created_at"] <= opened and uses[label["name"]] >= LABEL_MIN_USES
    )


# --- reviewers ---------------------------------------------------------------------------


def path_keys(files: list[str]) -> dict[str, int]:
    """Directory prefixes up to PATH_DEPTH and the file itself, each with its weight (depth;
    the file gets PATH_DEPTH + 1)."""
    keys: dict[str, int] = {}
    for f in files[:MAX_FILES]:
        parts = f.split("/")
        for d in range(1, min(len(parts), PATH_DEPTH + 1)):
            key = "/".join(parts[:d]) + "/"
            keys[key] = max(keys.get(key, 0), d)
        keys[f] = PATH_DEPTH + 1
    return keys


def rank_reviewers(rows: list[dict]) -> dict[tuple[str, int], list[dict]]:
    """Top reviewers per PR from path history, walking events in time order: a PR's
    reviewers enter the history at its merge, and a PR is ranked at its opening."""
    out = {}
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    for repo_rows in by_repo.values():
        events = [(ts(r["created_at"]), 1, r) for r in repo_rows]
        events += [(ts(r["merged_at"]), 0, r) for r in repo_rows if r["merged_at"]]
        # Same timestamp: merges (0) before openings (1); nothing merges and opens at once.
        events.sort(key=lambda e: (e[0], e[1], e[2]["number"]))
        history: dict[str, Counter] = defaultdict(Counter)
        for _, kind, r in events:
            keys = path_keys(r["changed_files"])
            if kind == 0:
                for key in keys:
                    history[key].update(r["reviewers"])
                continue
            scores: Counter = Counter()
            for key, weight in keys.items():
                for login, n in history[key].items():
                    scores[login] += weight * n
            scores.pop(r["author"], None)
            ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_REVIEWERS]
            out[(r["repo"], r["number"])] = [{"login": k, "score": v} for k, v in ranked]
    return out


# --- issues ------------------------------------------------------------------------------


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if len(t) > 1 and t not in _STOP]


def named_issues(text: str, repo: str) -> list[int]:
    """Issue numbers the text names, in order: `#N` or a github.com issue URL of this repo."""
    out = []
    for m in _ISSUE_REF.finditer(text or ""):
        if m["n"]:
            out.append(int(m["n"]))
        elif m["repo"].lower() == repo.lower():
            out.append(int(m["u"]))
    return list(dict.fromkeys(out))


class IssueIndex:
    """BM25 over one repo's issues, with an open-at-time mask."""

    def __init__(self, issues: list[dict]):
        self.issues = sorted(issues, key=lambda i: i["number"])
        self.numbers = np.array([i["number"] for i in self.issues])
        self.row_of = {n: k for k, n in enumerate(self.numbers.tolist())}
        self.created = np.array([ts(i["created_at"]) for i in self.issues])
        self.closed = np.array(
            [ts(i["closed_at"]) if i["closed_at"] else math.inf for i in self.issues]
        )
        docs = [tokens(f"{i['title']} {i['body']}") for i in self.issues]
        lengths = np.array([len(d) for d in docs], dtype=float)
        stats_docs = self.created < ts(VOCAB_BEFORE + "T00:00:00Z")
        n_stats = max(int(stats_docs.sum()), 1)
        avgdl = float(lengths[stats_docs].mean()) if stats_docs.any() else 1.0
        df: Counter = Counter()
        for d, counted in zip(docs, stats_docs, strict=True):
            if counted:
                df.update(set(d))
        self.postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths / avgdl)
        grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for k, d in enumerate(docs):
            for term, tf in Counter(d).items():
                grouped[term].append((k, tf))
        for term, plist in grouped.items():
            idf = math.log(1 + (n_stats - df[term] + 0.5) / (df[term] + 0.5))
            rows = np.array([p[0] for p in plist])
            tf = np.array([p[1] for p in plist], dtype=float)
            self.postings[term] = (rows, idf * tf * (BM25_K1 + 1) / (tf + norm[rows]))

    def open_at(self, t: float) -> np.ndarray:
        return (self.created < t) & (self.closed > t)

    def rank(self, query: str, opened: str, named: list[int], k: int = TOP_ISSUES) -> list[dict]:
        t = ts(opened)
        is_open = self.open_at(t)
        first = [n for n in named if n in self.row_of and is_open[self.row_of[n]]][:k]
        scores = np.zeros(len(self.issues))
        for term in set(tokens(query)):
            if term in self.postings:
                rows, contrib = self.postings[term]
                scores[rows] += contrib
        scores[~is_open] = -1.0
        for n in first:
            scores[self.row_of[n]] = -1.0
        top = np.argsort(-scores, kind="stable")[: k - len(first)]
        out = [{"number": n, "score": None, "named": True} for n in first]
        out += [
            {"number": int(self.numbers[j]), "score": round(float(scores[j]), 4), "named": False}
            for j in top
            if scores[j] > 0
        ]
        return out

    def title(self, number: int) -> str:
        return self.issues[self.row_of[number]]["title"]


def issue_query(row: dict) -> str:
    return " ".join([row["title"], row["body"] or "", " ".join(row["changed_files"][:MAX_FILES])])


# --- per PR ------------------------------------------------------------------------------


def candidate_row(row: dict, labels: list[str], reviewers: list[dict], index: IssueIndex) -> dict:
    text = f"{row['title']}\n{row['body'] or ''}"
    issues = index.rank(issue_query(row), row["created_at"], named_issues(text, row["repo"]))
    for i in issues:
        i["title"] = index.title(i["number"])
    return {
        "repo": row["repo"],
        "number": row["number"],
        "split": row["split"],
        "labels": labels,
        "reviewers": reviewers,
        "issues": issues,
    }


def ceilings(rows: list[dict], cands: dict[tuple[str, int], dict]) -> dict:
    """How often the truth is in the lists at all, and the rank-only (rules) recall."""
    n_rev = rev20 = rev3 = 0
    n_iss = iss20 = iss5 = 0
    n_lab = lab_in = 0
    for r in rows:
        c = cands[(r["repo"], r["number"])]
        if r["reviewers"]:
            n_rev += 1
            logins = [x["login"] for x in c["reviewers"]]
            rev20 += bool(set(r["reviewers"]) & set(logins))
            rev3 += bool(set(r["reviewers"]) & set(logins[:3]))
        truth_issues = {i["number"] for i in r["issues"] if i["same_repo"]}
        if truth_issues:
            n_iss += 1
            numbers = [x["number"] for x in c["issues"]]
            iss20 += bool(truth_issues & set(numbers))
            iss5 += bool(truth_issues & set(numbers[:5]))
        targets = {
            a["label"]
            for a in r["label_attribution"]
            if a["label"] in r["labels"] and not a["after_merge"]
        }
        n_lab += len(targets)
        lab_in += len(targets & set(c["labels"]))

    def rate(k: int, n: int) -> dict:
        return {"hits": k, "n": n, "rate": round(k / n, 4) if n else None}

    return {
        "reviewer_in_top20": rate(rev20, n_rev),
        "reviewer_in_top3": rate(rev3, n_rev),
        "issue_in_top20": rate(iss20, n_iss),
        "issue_in_top5": rate(iss5, n_iss),
        "target_labels_in_vocabulary": rate(lab_in, n_lab),
    }
