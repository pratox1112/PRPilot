import random
from dataclasses import dataclass

import pytest

from prpilot_data import secrets_eval as se

DIFF = "\n".join(
    [
        "diff --git a/app/settings.py b/app/settings.py",
        "@@ -1,2 +1,2 @@",
        " import os",
        "-DEBUG = True",
    ]
)


@dataclass(frozen=True)
class F:
    line: int
    type: str


def test_insert_places_one_added_line_after_the_hunk_header():
    new, lines, secret = se.insert(DIFF, "stripe_secret_key", random.Random(0))
    out = new.split("\n")
    assert lines == [2] and out[2] == f'+stripe_key = "{secret}"'
    assert out[3:] == DIFF.split("\n")[2:]


def test_private_key_is_a_block_and_go_uses_short_assignment():
    new, lines, _ = se.insert(DIFF, "private_key", random.Random(0))
    out = new.split("\n")
    assert len(lines) == 8 and out[lines[0]].startswith("+-----BEGIN ")
    go = DIFF.replace("app/settings.py", "pkg/a.go")
    new, lines, secret = se.insert(go, "github_pat_classic", random.Random(0))
    assert new.split("\n")[lines[0]] == f'+github_token := "{secret}"'


def test_formats_are_seeded_and_shaped():
    a = {f: se.FORMATS[f][1](random.Random(1)) for f in se.FORMATS}
    b = {f: se.FORMATS[f][1](random.Random(1)) for f in se.FORMATS}
    assert a == b
    assert len(a["aws_access_key_id"]) == 20 and a["aws_access_key_id"].startswith("AK")
    assert a["jwt"].count(".") == 2


def test_score_counts_hits_on_inserted_lines_only():
    sample = {"lines": [2, 3]}
    assert se.score(sample, [F(3, "Secret Keyword"), F(9, "Hex High Entropy String")]) == {
        "hit": True,
        "true_findings": 1,
        "false_findings": 1,
        "types": ["Secret Keyword"],
    }
    assert not se.score(sample, [])["hit"]


def test_build_cycles_formats_and_skips_diffs_without_syntax():
    diffs = [{"key": f"o/r#{i}", "diff": DIFF} for i in range(30)] + [
        {"key": "o/r#99", "diff": "diff --git a/x.png b/x.png\n@@\n+b"}
    ]
    out = se.build(diffs, 24, seed=0)
    assert [s["format"] for s in out[:12]] == list(se.FORMATS)
    assert all(s["key"] != "o/r#99" for s in out)


def test_scanner_finds_typical_formats():
    pytest.importorskip("detect_secrets")
    from prpilot_core.screening.secrets import scan_diff

    for fmt in ("aws_access_key_id", "github_pat_classic", "private_key", "password_assignment"):
        sample = {"lines": None}
        new, lines, _ = se.insert(DIFF, fmt, random.Random(3))
        sample["lines"] = lines
        assert se.score(sample, scan_diff(new))["hit"], fmt
