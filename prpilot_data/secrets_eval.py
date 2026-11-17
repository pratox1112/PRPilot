"""Synthetic secrets in real diffs, to measure the secret scanner (roadmap §7.3).

Fake secrets in realistic formats are generated at run time (seeded; nothing secret-shaped
is written in this file, so the repo's own gitleaks check stays quiet) and inserted as one
added line into a sample of test-split diffs, at a known line. Because we placed them, the
ground truth is known by construction:
- recall: inserted secrets the scanner reports on their line;
- precision: of everything the scanner reports in those diffs, the share on inserted lines
  (other findings are counted as false alarms, although a real diff can contain a real key);
- the flag rate on all unmodified test diffs, for how often a normal PR gets a redaction.
"""

import random
import string

from prpilot_data.redteam import comment_target

ALNUM = string.ascii_letters + string.digits
UPPER_DIGITS = string.ascii_uppercase + string.digits
HEX = "0123456789abcdef"
B64 = ALNUM + "+/"


def _r(rng: random.Random, chars: str, n: int) -> str:
    return "".join(rng.choice(chars) for _ in range(n))


def _jwt(rng: random.Random) -> str:
    import base64
    import json

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    head = part({"alg": "HS256", "typ": "JWT"})
    body = part({"sub": _r(rng, string.digits, 10), "name": "ci-bot", "iat": 1700000000})
    return f"{head}.{body}.{_r(rng, ALNUM + '-_', 43)}"


def _private_key(rng: random.Random) -> list[str]:
    kind = "RSA " + "PRIVATE KEY"
    lines = ["-----BEGIN " + kind + "-----"]
    lines += [_r(rng, B64, 64) for _ in range(6)]
    lines.append("-----END " + kind + "-----")
    return lines


# name -> (variable name, function producing the value or the lines of a block)
FORMATS = {
    "aws_access_key_id": ("aws_access_key_id", lambda g: "AK" + "IA" + _r(g, UPPER_DIGITS, 16)),
    "aws_secret_access_key": ("aws_secret_access_key", lambda g: _r(g, B64, 40)),
    "github_pat_classic": ("github_token", lambda g: "gh" + "p_" + _r(g, ALNUM, 36)),
    "github_pat_fine": ("gh_token", lambda g: "github" + "_pat_" + _r(g, ALNUM + "_", 82)),
    "slack_bot_token": (
        "slack_token",
        lambda g: "xo" + "xb-" + _r(g, string.digits, 12) + "-" + _r(g, string.digits, 13)
        + "-" + _r(g, ALNUM, 24),
    ),
    "stripe_secret_key": ("stripe_key", lambda g: "sk" + "_live_" + _r(g, ALNUM, 24)),
    "openai_api_key": ("openai_api_key", lambda g: "sk" + "-" + _r(g, ALNUM, 20) + "T3Blbk" + "FJ"
                       + _r(g, ALNUM, 20)),
    "google_api_key": ("google_api_key", lambda g: "AI" + "za" + _r(g, ALNUM + "-_", 35)),
    "jwt": ("auth_jwt", _jwt),
    "password_assignment": ("db_password", lambda g: _r(g, ALNUM + "!@#%", 16)),
    "generic_hex_secret": ("api_secret", lambda g: _r(g, HEX, 40)),
    "private_key": (None, _private_key),
}  # fmt: skip


def insert(diff: str, fmt: str, rng: random.Random) -> tuple[str, list[int], str]:
    """(new diff, indexes of the inserted lines, the secret) for one synthetic secret."""
    at, prefix = comment_target(diff)  # first hunk of the first file with known syntax
    var, make = FORMATS[fmt]
    value = make(rng)
    if fmt == "private_key":
        added = [f"+{line}" for line in value]
        secret = value[1]
    else:
        quote = "" if prefix == ".." else '"'
        assign = ":=" if prefix == "//" else "="
        added = [f"+{var} {assign} {quote}{value}{quote}"]
        secret = value
    lines = diff.split("\n")
    new = [*lines[: at + 1], *added, *lines[at + 1 :]]
    return "\n".join(new), list(range(at + 1, at + 1 + len(added))), secret


def build(diffs: list[dict], n: int, seed: int) -> list[dict]:
    """n seeded test diffs with a known comment syntax, each with one synthetic secret; the
    formats cycle so each appears equally often."""
    rng = random.Random(seed)
    pool = sorted(
        (d for d in diffs if d["diff"] and comment_target(d["diff"])), key=lambda d: d["key"]
    )
    picked = sorted(rng.sample(pool, n), key=lambda d: d["key"])
    names = list(FORMATS)
    out = []
    for i, d in enumerate(picked):
        fmt = names[i % len(names)]
        new, lines, secret = insert(d["diff"], fmt, rng)
        out.append({"key": d["key"], "format": fmt, "diff": new, "lines": lines, "secret": secret})
    return out


def score(sample: dict, findings: list) -> dict:
    """recall hit: a finding on an inserted line; other findings are false alarms."""
    inserted = set(sample["lines"])
    on = [f for f in findings if f.line in inserted]
    off = [f for f in findings if f.line not in inserted]
    return {"hit": bool(on), "true_findings": len(on), "false_findings": len(off),
            "types": sorted({f.type for f in on})}  # fmt: skip
