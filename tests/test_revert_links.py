import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

from revert_links import (  # noqa: E402
    extract_refs,
    is_revert_title,
    quoted_original_title,
    title_matches,
)

REPO = "kubernetes/kubernetes"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ('Revert "Fix flaky test"', True),
        ("revert: drop cache", True),
        ("[release-1.31] Revert #123", True),
        ("Reverting #42 because it broke CI", True),
        ("Revertible operations in the scheduler", False),
        ("Add revert command", False),
        ("Prevent crash", False),
    ],
)
def test_is_revert_title(title, expected):
    assert is_revert_title(title) is expected


def test_github_revert_button_body():
    refs = extract_refs(REPO, 'Revert "X"', "Reverts kubernetes/kubernetes#12345", [])
    assert refs.explicit_prs == {12345}


def test_explicit_short_and_url_forms():
    refs = extract_refs(
        REPO,
        "Revert #11",
        "This reverts https://github.com/kubernetes/kubernetes/pull/22 as well.",
        [],
    )
    assert refs.explicit_prs == {11, 22}


def test_other_repo_reference_is_ignored():
    refs = extract_refs(REPO, 'Revert "X"', "Reverts cilium/cilium#7", [])
    assert refs.explicit_prs == frozenset()
    assert refs.candidate_prs == frozenset()


def test_commit_sha_from_commit_message():
    msg = 'Revert "Fix X"\n\nThis reverts commit 0123abcd4567ef89.'
    refs = extract_refs(REPO, 'Revert "Fix X"', "", [msg])
    assert refs.commit_shas == {"0123abcd4567ef89"}


def test_bare_reference_is_only_a_candidate():
    refs = extract_refs(REPO, 'Revert "Fix X"', "Broke CI, see #99 and issue #100.", [])
    assert refs.explicit_prs == frozenset()
    assert refs.candidate_prs == {99, 100}


def test_quoted_title_and_matching():
    assert quoted_original_title('Revert "Fix flaky test"') == "Fix flaky test"
    assert title_matches('Revert "Fix flaky test"', "fix flaky test")
    assert not title_matches('Revert "Fix flaky test"', "Fix flaky test in e2e")
    assert title_matches("Revert: Fix flaky test (#99)", "Fix flaky test")
    assert not title_matches("Revert something", "")
