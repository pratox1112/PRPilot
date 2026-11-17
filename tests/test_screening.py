from dataclasses import dataclass

import pytest

from prpilot_core.agents.triage_format import MAX_BODY_CHARS, MAX_FILES_LISTED, PRContext
from prpilot_core.screening.pii import Span, is_mention, merge_spans, redact_spans
from prpilot_core.screening.screen import guard_fields, screen
from prpilot_core.screening.secrets import Finding, file_sections, redact_diff

# AWS's documented example key, built from pieces so the repo's gitleaks check does not flag it.
AWS_EXAMPLE = "AKIA" + "IOSFODNN7" + "EXAMPLE"

DIFF = "\n".join(
    [
        "diff --git a/app/config.py b/app/config.py",
        "index 111..222 100644",
        "--- a/app/config.py",
        "+++ b/app/config.py",
        "@@ -1,2 +1,3 @@",
        " import os",
        f'+AWS_KEY = "{AWS_EXAMPLE}"',
        "-OLD = 1",
        "diff --git a/README.md b/README.md",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1 +1 @@",
        "+hello",
    ]
)


def pr(**over) -> PRContext:
    base = dict(
        title="Fix crash",
        body="Thanks Alice Smith, mail alice@example.com",
        author="bob",
        base_branch="main",
        changed_files=("app/config.py",),
        diff=DIFF,
    )
    return PRContext(**{**base, **over})


def test_file_sections_keep_only_content_lines():
    assert file_sections(DIFF) == [("app/config.py", [5, 6, 7]), ("README.md", [12])]


def test_redact_diff_keeps_prefix_and_replaces_secret():
    out = redact_diff(DIFF, [Finding(6, "AWS Access Key", AWS_EXAMPLE)])
    assert out.split("\n")[6] == '+AWS_KEY = "<SECRET:AWS Access Key>"'
    assert "AKIA" not in out


def test_redact_private_key_block():
    diff = "\n".join(
        [
            "@@",
            "+-----BEGIN RSA PRIVATE KEY-----",
            "+MIIEow",
            "+-----END RSA PRIVATE KEY-----",
            " x",
        ]
    )
    out = redact_diff(diff, [Finding(1, "Private Key", "")]).split("\n")
    assert out == ["@@", "+<SECRET:Private Key>", "+", "+", " x"]


def test_merge_and_redact_spans():
    spans = merge_spans(
        [
            Span(0, 11, "PERSON", 0.85),
            Span(6, 11, "PERSON", 0.6),
            Span(13, 18, "EMAIL_ADDRESS", 1.0),
        ]
    )
    assert spans == [Span(0, 11, "PERSON", 0.85), Span(13, 18, "EMAIL_ADDRESS", 1.0)]
    assert redact_spans("Alice Smith, a@b.c ok", spans) == "<PERSON>, <EMAIL_ADDRESS> ok"


def test_mentions_are_kept_but_emails_are_not():
    text = "cc @seberg and mail alice@example.com"
    assert is_mention(text, Span(0, 10, "PERSON", 0.9))  # "cc @seberg"
    assert is_mention(text, Span(4, 10, "PERSON", 0.9))  # "seberg", "@" just outside
    assert not is_mention(text, Span(20, 37, "PERSON", 0.9))  # inside an email address
    assert not is_mention(text, Span(3, 10, "EMAIL_ADDRESS", 0.9))


@dataclass
class FakeResult:
    score: float


class FakeGuard:
    def __init__(self, bad: str):
        self.bad, self.seen = bad, []

    def score(self, text):
        self.seen.append(text)
        return FakeResult(0.99 if self.bad in text else 0.01)


class FakePII:
    def redact(self, text):
        out = text.replace("alice@example.com", "<EMAIL_ADDRESS>")
        return out, [Span(0, 1, "EMAIL_ADDRESS", 1.0)] if out != text else []


def test_guard_sees_the_body_as_the_prompt_shows_it():
    fields = guard_fields(pr(body="x" * (MAX_BODY_CHARS + 50)))
    assert fields["body"].startswith("x" * MAX_BODY_CHARS) and "truncated" in fields["body"]
    assert set(fields) == {"title", "body", "changed_files", "diff"}
    files = tuple(f"f{i}.go" for i in range(MAX_FILES_LISTED + 7))
    listed = guard_fields(pr(changed_files=files))["changed_files"].split("\n")
    assert len(listed) == MAX_FILES_LISTED + 1 and listed[-1] == "[... and 7 more files]"


def test_screen_quarantines_on_body_files_or_diff():
    for field, value in (("body", "ignore previous"), ("diff", "+# ignore previous")):
        res = screen(
            pr(**{field: value}), FakeGuard("ignore previous"), FakePII(), scan_secrets=False
        )
        assert res.quarantined and res.pr is None and res.field_scores[field] == 0.99
    res = screen(
        pr(changed_files=("ignore previous.md",)), FakeGuard("ignore previous"), scan_secrets=False
    )
    assert res.quarantined


def test_title_is_scored_but_does_not_quarantine_alone():
    res = screen(pr(title="Remove unused code"), FakeGuard("Remove unused"), scan_secrets=False)
    assert not res.quarantined and res.field_scores["title"] == 0.99 and res.guard_score == 0.99


def test_screen_redacts_when_clean():
    res = screen(pr(), FakeGuard("nothing matches"), FakePII(), scan_secrets=False)
    assert not res.quarantined
    assert res.pr.body == "Thanks Alice Smith, mail <EMAIL_ADDRESS>"
    assert res.pii == {"EMAIL_ADDRESS": 1}
    assert res.pr.diff == DIFF  # secrets scan off


def test_screen_threshold_is_inclusive():
    assert screen(pr(), FakeGuard("Thanks"), scan_secrets=False, threshold=0.99).quarantined
    assert not screen(pr(), FakeGuard("Thanks"), scan_secrets=False, threshold=0.995).quarantined


def test_classifier_sees_the_guard_fields_as_data_blocks():
    from prpilot_core.screening.classifier import classifier_messages, parse_verdict

    msgs = classifier_messages(pr(title="a </pr_title> b"))
    assert msgs[0]["role"] == "system" and "INJECTION or SAFE" in msgs[0]["content"]
    user = msgs[1]["content"]
    for tag in ("pr_title", "pr_body", "pr_changed_files", "pr_diff"):
        assert f"<{tag}>" in user and f"</{tag}>" in user
    assert "a </ pr_title> b" in user  # cannot close the block early
    assert parse_verdict(" injection.") == "INJECTION"
    assert parse_verdict("**SAFE**") == "SAFE"
    assert parse_verdict("I think it is safe") is None


def test_scan_diff_finds_and_maps_secrets():
    pytest.importorskip("detect_secrets")
    from prpilot_core.screening.secrets import scan_diff

    found = scan_diff(DIFF)
    assert [(f.line, f.type) for f in found] == [(6, "AWS Access Key")]
    res = screen(pr(), FakeGuard("nothing"), None, scan_secrets=True)
    assert "AKIA" not in res.pr.diff and res.secrets == tuple(found)
