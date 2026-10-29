from prpilot_data.ground_truth import (
    issue_truth,
    label_truth,
    links_query,
    mentions_issue,
    repo_bots,
    reviewer_truth,
    summarize,
    truth_row,
)
from prpilot_data.labels import RepoBots

BOTS = RepoBots(bot_logins=frozenset({"k8s-ci-robot"}), command_bots=frozenset({"k8s-ci-robot"}))
AUTHOR = {"__typename": "User", "login": "alice"}
BOB = {"__typename": "User", "login": "bob"}
CAROL = {"__typename": "User", "login": "carol"}
ROBOT = {"__typename": "User", "login": "k8s-ci-robot"}
APP = {"__typename": "Bot", "login": "copilot-pull-request-reviewer"}
PRIORITY = ["bug", "feature", "dependency", "ci/build", "test", "docs", "refactor", "other"]
MAPPING = {"kind/bug": "bug", "kind/cleanup": "refactor", "kind/feature": "feature"}


def node(**over):
    base = {
        "repo": "o/r",
        "number": 9,
        "title": "Fix it",
        "body": "",
        "createdAt": "2025-01-01T00:00:00Z",
        "mergedAt": "2025-01-05T00:00:00Z",
        "author": AUTHOR,
        "authorAssociation": "MEMBER",
        "headRefOid": "h",
        "labelEvents": {"nodes": []},
        "comments": {"nodes": []},
        "reviewRequests": {"nodes": []},
        "reviews": {"nodes": []},
        "userContentEdits": {"totalCount": 0, "nodes": []},
        "timelineItems": {"totalCount": 0, "nodes": []},
        "commits": {"totalCount": 0, "nodes": []},
        "closingIssuesReferences": {"totalCount": 0, "nodes": []},
    }
    return {**base, **over}


def labeled(name, actor, at="2025-01-02T00:00:00Z", typename="LabeledEvent"):
    return {"__typename": typename, "createdAt": at, "actor": actor, "label": {"name": name}}


def request(reviewer, actor, at):
    return {"createdAt": at, "actor": actor, "requestedReviewer": reviewer}


def review(author, at="2025-01-03T00:00:00Z"):
    return {"submittedAt": at, "state": "APPROVED", "author": author}


# --- labels and type ---------------------------------------------------------------------


def test_type_from_human_labels_by_priority_and_bot_labels_ignored():
    events = [
        labeled("kind/cleanup", BOB),
        labeled("kind/bug", BOB),
        labeled("kind/feature", APP),  # bot-applied: not a target
        labeled("area/x", BOB),  # human label, not a type label
    ]
    out = label_truth(node(labelEvents={"nodes": events}), BOTS, MAPPING, PRIORITY)
    assert out["labels"] == ["area/x", "kind/bug", "kind/cleanup"]
    assert out["types"] == ["bug", "refactor"] and out["primary_type"] == "bug"
    assert out["label_attribution"]["kind/feature"]["kind"] == "bot"


def test_slash_command_label_counts_as_human():
    events = [labeled("kind/bug", ROBOT, at="2025-01-01T00:01:00Z")]
    n = node(labelEvents={"nodes": events}, body="Fixes a crash.\n/kind bug\n")
    out = label_truth(n, BOTS, MAPPING, PRIORITY)
    assert out["primary_type"] == "bug"
    assert out["label_attribution"]["kind/bug"]["applied_by"] == "alice"


def test_no_mapped_label_means_no_type_not_other():
    out = label_truth(
        node(labelEvents={"nodes": [labeled("release-note/misc", BOB)]}), BOTS, {}, PRIORITY
    )
    assert out["primary_type"] is None and out["types"] == []


def test_removed_label_is_not_a_target():
    events = [
        labeled("kind/bug", BOB),
        labeled("kind/bug", BOB, at="2025-01-03T00:00:00Z", typename="UnlabeledEvent"),
    ]
    assert label_truth(node(labelEvents={"nodes": events}), BOTS, MAPPING, PRIORITY)["labels"] == []


# --- reviewers ---------------------------------------------------------------------------


def test_codeowners_requests_at_open_are_excluded_later_author_requests_kept():
    requests = [
        request(BOB, AUTHOR, "2025-01-01T00:00:05Z"),  # CODEOWNERS at open
        request(CAROL, AUTHOR, "2025-01-02T00:00:00Z"),  # author asked a day later
    ]
    out = reviewer_truth(node(reviewRequests={"nodes": requests}), BOTS)
    assert out["reviewers"] == ["carol"]
    assert out["excluded_requests"] == {"codeowners_at_open": 1}


def test_human_request_from_someone_else_at_open_counts():
    out = reviewer_truth(
        node(reviewRequests={"nodes": [request(CAROL, BOB, "2025-01-01T00:00:30Z")]}), BOTS
    )
    assert out["reviewer_evidence"] == {"carol": ["human_request"]}


def test_bot_requests_count_only_with_a_human_cc_command():
    requests = [
        request(BOB, ROBOT, "2025-01-01T00:10:00Z"),  # blunderbuss auto-assignment
        request(CAROL, ROBOT, "2025-01-02T00:00:00Z"),  # after a human /cc
    ]
    comments = [{"createdAt": "2025-01-01T23:59:00Z", "author": BOB, "body": "/cc @carol"}]
    n = node(reviewRequests={"nodes": requests}, comments={"nodes": comments})
    out = reviewer_truth(n, BOTS)
    assert out["reviewer_evidence"] == {"carol": ["cc_command"]}
    assert out["excluded_requests"] == {"bot_request": 1}


def test_cc_written_by_a_bot_does_not_count():
    comments = [{"createdAt": "2025-01-01T00:01:00Z", "author": ROBOT, "body": "/cc @carol"}]
    n = node(
        reviewRequests={"nodes": [request(CAROL, ROBOT, "2025-01-01T00:02:00Z")]},
        comments={"nodes": comments},
    )
    assert reviewer_truth(n, BOTS)["reviewers"] == []


def test_reviews_count_but_not_author_bots_pending_or_after_merge():
    reviews = [
        review(BOB),
        review(AUTHOR),  # replying in their own PR
        review(APP),
        review(CAROL, at=None),  # pending, never submitted
        review({"__typename": "User", "login": "dave"}, at="2025-02-01T00:00:00Z"),  # post-merge
    ]
    out = reviewer_truth(node(reviews={"nodes": reviews}), BOTS)
    assert out["reviewers"] == ["bob"] and out["reviewer_evidence"] == {"bob": ["reviewed"]}


def test_requested_and_reviewed_evidence_merge():
    n = node(
        reviewRequests={"nodes": [request(BOB, CAROL, "2025-01-02T00:00:00Z")]},
        reviews={"nodes": [review(BOB)]},
    )
    assert reviewer_truth(n, BOTS)["reviewer_evidence"] == {"bob": ["human_request", "reviewed"]}


def test_team_or_hidden_requests_are_counted_not_used():
    n = node(reviewRequests={"nodes": [request(None, BOB, "2025-01-02T00:00:00Z")]})
    out = reviewer_truth(n, BOTS)
    assert out["reviewers"] == [] and out["excluded_requests"] == {"team_or_hidden": 1}


# --- issues ------------------------------------------------------------------------------


def test_mentions_issue_forms():
    assert mentions_issue("Fixes #12", "o/r", 12)
    assert mentions_issue("closes o/r#12.", "o/r", 12)
    assert mentions_issue("see https://github.com/o/r/issues/12", "o/r", 12)
    assert not mentions_issue("Fixes #123", "o/r", 12)
    assert not mentions_issue("other/repo#12", "o/r", 12)
    assert not mentions_issue("color: #12ab34", "o/r", 12)


def test_issue_truth_uses_open_time_body():
    edits = {"totalCount": 1, "nodes": [{"createdAt": "2025-01-02T00:00:00Z", "diff": "Fixes #4"}]}
    n = node(body="Edited later, no mention", userContentEdits=edits)
    links = [
        {"number": 4, "repository": {"nameWithOwner": "o/r"}},
        {"number": 7, "repository": {"nameWithOwner": "other/x"}},
    ]
    out = issue_truth(n, links)["issues"]
    assert out[0] == {"repo": "o/r", "number": 4, "same_repo": True, "mentioned_at_open": True}
    assert out[1]["same_repo"] is False and out[1]["mentioned_at_open"] is False


def test_links_query_aliases_each_pr():
    q = links_query([3, 5])
    assert "pr3: pullRequest(number: 3)" in q and "nameWithOwner" in q


# --- whole row ---------------------------------------------------------------------------


def test_truth_row_and_summary():
    bot_pr = node(number=10, author={"__typename": "Bot", "login": "dependabot"})
    rows = [
        truth_row(
            node(labelEvents={"nodes": [labeled("kind/bug", BOB)]}), BOTS, MAPPING, PRIORITY, []
        ),
        truth_row(bot_pr, BOTS, MAPPING, PRIORITY, []),
    ]
    assert rows[1]["author_is_bot"] and rows[0]["primary_type"] == "bug"
    s = summarize(rows)
    assert s["prs"] == 2 and s["bot_authored"] == 1 and s["with_type"] == 1
    assert s["primary_type"] == {"bug": 1, "none": 1}


def test_repo_bots_merges_global_list():
    cfg = {
        "global": {"bot_logins": ["g"]},
        "repos": {"o/r": {"bot_logins": ["x"], "command_bots": ["x"]}},
    }
    bots = repo_bots(cfg, "o/r")
    assert bots.bot_logins == {"g", "x"} and bots.command_bots == {"x"}
