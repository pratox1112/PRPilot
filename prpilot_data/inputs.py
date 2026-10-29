"""Input preparation for teacher/student prompts (feasibility version of roadmap §4.4).

- `strip_label_commands`: removes slash-command lines (`/kind bug`, `/label x`, `/area y`)
  from PR text. In repos like kubernetes these commands are what apply the type label, so
  leaving them in would hand the model the answer ("text that reveals the target", §4.4).
- `truncate_diff`: fits a unified diff into a character budget, keeping the per-file
  `diff --git` and `@@` hunk headers first (so the model sees every touched file and hunk
  location), then as many hunk bodies as fit, in order.
- `fit_diff_to_tokens`: the same policy with the budget counted in student tokens.
- `changed_files`: file paths from the `diff --git` headers.
"""

import re
from collections.abc import Callable

_COMMAND_LINE = re.compile(
    r"^[ \t]*/[a-z][\w-]*(?:[ \t]+\S.*)?[ \t]*$", re.IGNORECASE | re.MULTILINE
)
_FILE_HEADER = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)
TRUNCATION_MARK = "\n[... diff truncated to fit the input budget ...]\n"


def strip_label_commands(text: str) -> str:
    stripped = _COMMAND_LINE.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def changed_files(diff: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m["b"] for m in _FILE_HEADER.finditer(diff or "")))


def _split_files(diff: str) -> list[str]:
    starts = [m.start() for m in _FILE_HEADER.finditer(diff)]
    return [diff[s:e] for s, e in zip(starts, starts[1:] + [len(diff)], strict=True)]


def truncate_diff(diff: str, max_chars: int) -> tuple[str, bool]:
    """Return (diff that fits in max_chars, whether anything was cut)."""
    diff = diff or ""
    if len(diff) <= max_chars:
        return diff, False
    files = _split_files(diff)
    headers = []
    for f in files:
        lines = f.split("\n")
        headers.append(lines[0])
        headers += [ln for ln in lines[1:] if ln.startswith("@@")]
    outline = "Files and hunks (outline):\n" + "\n".join(headers) + "\n\nHunks:\n"
    if len(outline) + len(TRUNCATION_MARK) >= max_chars:
        cut = max_chars - len(TRUNCATION_MARK)
        return outline[: max(cut, 0)] + TRUNCATION_MARK, True
    budget = max_chars - len(outline) - len(TRUNCATION_MARK)
    body = []
    for f in files:
        if len(f) <= budget:
            body.append(f)
            budget -= len(f)
        else:
            body.append(f[:budget])
            break
    return outline + "".join(body) + TRUNCATION_MARK, True


MAX_CHARS_PER_TOKEN = 12  # far above code's ~3-4, so the search range always holds the answer
MAX_COUNTED_CHARS = 2_000_000  # beyond this the full diff is not tokenized (lockfiles, vendoring)


def fit_diff_to_tokens(
    diff: str, max_tokens: int, count_tokens: Callable[[str], int]
) -> tuple[str, bool, int | None]:
    """`truncate_diff` with the budget in tokens of the student's tokenizer (Phase 1 decision 7).

    Returns (diff that fits, whether anything was cut, tokens in the full diff or None when the
    diff is too large to count). Bisects for the largest character budget whose truncated diff
    still fits, so the outline-first policy is the same as `truncate_diff`.
    """
    diff = diff or ""
    full = count_tokens(diff) if len(diff) <= MAX_COUNTED_CHARS else None
    if full is not None and full <= max_tokens:
        return diff, False, full
    lo, hi = 0, min(len(diff) - 1, max_tokens * MAX_CHARS_PER_TOKEN)
    best = TRUNCATION_MARK
    while lo < hi:
        mid = (lo + hi + 1) // 2
        text, _ = truncate_diff(diff, mid)
        if count_tokens(text) <= max_tokens:
            lo, best = mid, text
        else:
            hi = mid - 1
    return best, True, full
