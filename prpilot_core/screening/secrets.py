"""Secret detection and redaction in PR diffs, with Yelp's detect-secrets (roadmap §7.3).

Each file in the diff is scanned on its own, under its real file name, so detect-secrets'
file-based filters apply (lock files, for example). Only content lines are scanned (context,
added and removed lines, without the leading `+`/`-`/space); a secret in a removed line has
still leaked. Findings map back to diff line numbers, and redaction replaces the secret with
`<SECRET:type>` (a private key block, from its BEGIN to its END line, as a whole).

detect-secrets is imported lazily: it lives in the `screening` dependency group.
"""

import os
import re
import tempfile
from dataclasses import dataclass

FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
CONTENT = re.compile(r"^[+\- ]")
SKIP = ("--- ", "+++ ", "index ", "new file", "deleted file", "similarity", "rename ", "Binary")
KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY")


@dataclass(frozen=True)
class Finding:
    line: int  # 0-based line index in the diff
    type: str  # detect-secrets type, e.g. "AWS Access Key"
    secret: str


def file_sections(diff: str) -> list[tuple[str, list[int]]]:
    """(file path, diff line indexes of its content lines) for each file in a unified diff."""
    sections: list[tuple[str, list[int]]] = []
    in_hunk = False
    for i, line in enumerate(diff.split("\n")):
        m = FILE_HEADER.match(line)
        if m:
            sections.append((m.group(2), []))
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif sections and in_hunk and CONTENT.match(line) and not line.startswith(SKIP):
            sections[-1][1].append(i)
    return sections


def scan_diff(diff: str) -> list[Finding]:
    from detect_secrets import SecretsCollection
    from detect_secrets.settings import default_settings

    lines = diff.split("\n")
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory() as tmp, default_settings():
        for n, (path, idxs) in enumerate(file_sections(diff)):
            if not idxs:
                continue
            # Keep the base name so file-type filters see it; prefix keeps names unique.
            fname = os.path.join(tmp, f"{n}_{os.path.basename(path) or 'file'}")
            with open(fname, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines[i][1:] for i in idxs) + "\n")
            collection = SecretsCollection()
            collection.scan_file(fname)
            for secrets in collection.data.values():
                for s in secrets:
                    findings.append(Finding(idxs[s.line_number - 1], s.type, s.secret_value or ""))
    return sorted(set(findings), key=lambda f: (f.line, f.type))


def redact_diff(diff: str, findings: list[Finding]) -> str:
    lines = diff.split("\n")
    for f in findings:
        if f.type == "Private Key":
            end = next((j for j in range(f.line, len(lines)) if KEY_END.search(lines[j])), f.line)
            lines[f.line] = lines[f.line][:1] + "<SECRET:Private Key>"
            for j in range(f.line + 1, end + 1):
                lines[j] = lines[j][:1]
        elif f.secret:
            lines[f.line] = lines[f.line].replace(f.secret, f"<SECRET:{f.type}>")
    return "\n".join(lines)
