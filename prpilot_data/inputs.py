"""Input preparation for teacher/student prompts (feasibility version of roadmap §4.4).

- `strip_label_commands`: removes slash-command lines (`/kind bug`, `/label x`, `/area y`)
  from PR text. In repos like kubernetes these commands are what apply the type label, so
  leaving them in would hand the model the answer ("text that reveals the target", §4.4).
- `truncate_diff`: fits a unified diff into a character budget, keeping the per-file
  `diff --git` and `@@` hunk headers first (so the model sees every touched file and hunk
  location), then as many hunk bodies as fit, in order.
- `changed_files`: file paths from the `diff --git` headers.
"""

import re

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
        lines = f.splitlines()
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
