"""Find which PR a revert PR reverts (roadmap §4.3, risk ground truth).

A revert PR links to its original in one of these ways:
- GitHub's revert button writes "Reverts owner/repo#123" in the body;
- `git revert` writes "This reverts commit <sha>." in the commit message;
- people write "Revert #123" / "reverts https://github.com/owner/repo/pull/123".
A bare "#123" elsewhere in the body is only a candidate: it is accepted when the original's
title appears inside the revert's title (e.g. `Revert "Fix flaky test"`).
"""

import re
from dataclasses import dataclass

REVERT_TITLE = re.compile(r"^\s*(\[[^\]]*\]\s*)*revert(?:s|ed|ing)?\b", re.IGNORECASE)
_EXPLICIT_PR = re.compile(
    r"\brevert(?:s|ed|ing)?\b[^\n#]{0,40}?"
    r"(?:(?P<repo>[\w.-]+/[\w.-]+)?#(?P<num>\d+)"
    r"|https://github\.com/(?P<urlrepo>[\w.-]+/[\w.-]+)/pull/(?P<urlnum>\d+))",
    re.IGNORECASE,
)
_REVERTS_COMMIT = re.compile(r"\bthis reverts commit (?P<sha>[0-9a-f]{7,40})\b", re.IGNORECASE)
_ANY_PR_REF = re.compile(
    r"(?:(?P<repo>[\w.-]+/[\w.-]+)?#(?P<num>\d+))"
    r"|https://github\.com/(?P<urlrepo>[\w.-]+/[\w.-]+)/pull/(?P<urlnum>\d+)"
)
_QUOTED_TITLE = re.compile(r'revert\w*\s*["“](?P<title>.+?)["”]\s*$', re.IGNORECASE)


@dataclass(frozen=True)
class Refs:
    explicit_prs: frozenset[int]
    commit_shas: frozenset[str]
    candidate_prs: frozenset[int]


def is_revert_title(title: str) -> bool:
    return bool(REVERT_TITLE.match(title or ""))


def _same_repo(found: str | None, repo: str) -> bool:
    return found is None or found.lower() == repo.lower()


def extract_refs(repo: str, title: str, body: str, commit_messages: list[str]) -> Refs:
    texts = [title or "", body or "", *commit_messages]
    explicit, shas, candidates = set(), set(), set()
    for text in texts:
        for m in _EXPLICIT_PR.finditer(text):
            r, n = (m["repo"], m["num"]) if m["num"] else (m["urlrepo"], m["urlnum"])
            if _same_repo(r, repo):
                explicit.add(int(n))
        shas.update(m["sha"].lower() for m in _REVERTS_COMMIT.finditer(text))
        for m in _ANY_PR_REF.finditer(text):
            r, n = (m["repo"], m["num"]) if m["num"] else (m["urlrepo"], m["urlnum"])
            if _same_repo(r, repo):
                candidates.add(int(n))
    return Refs(frozenset(explicit), frozenset(shas), frozenset(candidates - explicit))


def quoted_original_title(revert_title: str) -> str | None:
    """'Revert "Fix flaky test"' -> 'Fix flaky test'."""
    m = _QUOTED_TITLE.search((revert_title or "").strip())
    return m["title"].strip() if m else None


def title_matches(revert_title: str, original_title: str) -> bool:
    quoted = quoted_original_title(revert_title)
    if quoted:
        return quoted.casefold() == (original_title or "").strip().casefold()
    return bool(original_title) and original_title.strip().casefold() in revert_title.casefold()
