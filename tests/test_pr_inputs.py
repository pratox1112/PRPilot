import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

from pr_inputs import (  # noqa: E402
    TRUNCATION_MARK,
    changed_files,
    strip_label_commands,
    truncate_diff,
)


def test_strips_command_lines_only():
    body = "Fixes a race.\n\n/kind bug\n  /area kubelet\n/label tide/squash\n\nSee /docs/x.md"
    out = strip_label_commands(body)
    assert "/kind" not in out and "/area" not in out and "/label" not in out
    assert "Fixes a race." in out and "See /docs/x.md" in out


def test_keeps_paths_and_inline_slashes():
    text = "/usr/bin is used\nRun `make test` in /tmp\n/kind feature"
    out = strip_label_commands(text)
    assert "/usr/bin is used" in out  # a path, not a command
    assert "Run `make test` in /tmp" in out
    assert "/kind feature" not in out


def test_empty_and_none():
    assert strip_label_commands("") == ""
    assert strip_label_commands(None) == ""


DIFF = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    "@@ -1,2 +1,3 @@ def f():\n"
    + "+x = 1\n"
    * 50
    + "diff --git a/docs/b.md b/docs/b.md\n--- a/docs/b.md\n+++ b/docs/b.md\n"
    "@@ -10 +10 @@\n-old\n+new\n"
)


def test_changed_files():
    assert changed_files(DIFF) == ("src/a.py", "docs/b.md")


def test_short_diff_untouched():
    assert truncate_diff(DIFF, 10_000) == (DIFF, False)


def test_truncation_keeps_outline_of_every_file():
    out, cut = truncate_diff(DIFF, 300)
    assert cut and len(out) <= 300
    assert "diff --git a/docs/b.md b/docs/b.md" in out
    assert "@@ -10 +10 @@" in out
    assert out.endswith(TRUNCATION_MARK)


def test_tiny_budget_still_bounded():
    out, cut = truncate_diff(DIFF, 60)
    assert cut and len(out) <= 60 + len(TRUNCATION_MARK)
