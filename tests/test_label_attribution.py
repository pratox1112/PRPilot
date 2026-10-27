import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

from label_attribution import RepoBots, attribute_labels, is_human_applied  # noqa: E402

BOTS = RepoBots(bot_logins=frozenset({"k8s-ci-robot"}), command_bots=frozenset({"k8s-ci-robot"}))
HUMAN = {"__typename": "User", "login": "alice"}
BOB = {"__typename": "User", "login": "bob"}
APP_BOT = {"__typename": "Bot", "login": "github-actions"}
CMD_BOT = {"__typename": "User", "login": "k8s-ci-robot"}


def labeled(name, actor, at, typename="LabeledEvent"):
    return {"__typename": typename, "createdAt": at, "actor": actor, "label": {"name": name}}


def comment(body, author, at):
    return {"__typename": "IssueComment", "createdAt": at, "author": author, "body": body}


def pr(*nodes, body="", author=HUMAN, merged="2025-01-10T00:00:00Z"):
    return {
        "author": author,
        "body": body,
        "createdAt": "2025-01-01T00:00:00Z",
        "mergedAt": merged,
        "timelineItems": {"nodes": list(nodes)},
    }


def test_human_label():
    a = attribute_labels(pr(labeled("bug", HUMAN, "2025-01-02T00:00:00Z")), BOTS)["bug"]
    assert a.kind == "human" and a.applied_by == "alice" and is_human_applied(a)


def test_app_bot_and_suffix_bot_and_listed_bot():
    nodes = [
        labeled("a", APP_BOT, "2025-01-02T00:00:00Z"),
        labeled("b", {"__typename": "User", "login": "renovate[bot]"}, "2025-01-02T00:00:00Z"),
        labeled("c", {"__typename": "User", "login": "k8s-ci-robot"}, "2025-01-02T00:00:00Z"),
    ]
    out = attribute_labels(pr(*nodes), BOTS)
    assert {k: v.kind for k, v in out.items()} == {"a": "bot", "b": "bot", "c": "bot"}


def test_removed_label_is_not_a_target():
    nodes = [
        labeled("bug", HUMAN, "2025-01-02T00:00:00Z"),
        labeled("bug", HUMAN, "2025-01-03T00:00:00Z", typename="UnlabeledEvent"),
    ]
    assert attribute_labels(pr(*nodes), BOTS) == {}


def test_relabel_uses_last_applier():
    nodes = [
        labeled("bug", APP_BOT, "2025-01-02T00:00:00Z"),
        labeled("bug", APP_BOT, "2025-01-03T00:00:00Z", typename="UnlabeledEvent"),
        labeled("bug", HUMAN, "2025-01-04T00:00:00Z"),
    ]
    assert attribute_labels(pr(*nodes), BOTS)["bug"].kind == "human"


def test_events_sorted_by_time():
    nodes = [
        labeled("bug", HUMAN, "2025-01-04T00:00:00Z"),
        labeled("bug", HUMAN, "2025-01-02T00:00:00Z", typename="UnlabeledEvent"),
    ]
    assert "bug" in attribute_labels(pr(*nodes), BOTS)


def test_slash_command_in_comment_credits_human():
    nodes = [
        comment("Looks good\n/kind bug", BOB, "2025-01-02T00:00:00Z"),
        labeled("kind/bug", CMD_BOT, "2025-01-02T00:01:00Z"),
    ]
    a = attribute_labels(pr(*nodes), BOTS)["kind/bug"]
    assert a.kind == "slash_command" and a.applied_by == "bob"


def test_slash_command_in_pr_body_credits_author():
    a = attribute_labels(
        pr(labeled("kind/feature", CMD_BOT, "2025-01-01T00:01:00Z"), body="/kind feature\n"), BOTS
    )["kind/feature"]
    assert a.kind == "slash_command" and a.applied_by == "alice"


def test_label_command_form():
    nodes = [
        comment("/label tide/merge-method-squash", HUMAN, "2025-01-02T00:00:00Z"),
        labeled("tide/merge-method-squash", CMD_BOT, "2025-01-02T00:01:00Z"),
    ]
    assert attribute_labels(pr(*nodes), BOTS)["tide/merge-method-squash"].kind == "slash_command"


def test_command_after_label_event_does_not_count():
    nodes = [
        labeled("kind/bug", CMD_BOT, "2025-01-02T00:00:00Z"),
        comment("/kind bug", HUMAN, "2025-01-03T00:00:00Z"),
    ]
    assert attribute_labels(pr(*nodes), BOTS)["kind/bug"].kind == "bot"


def test_command_by_bot_does_not_count():
    nodes = [
        comment("/kind bug", APP_BOT, "2025-01-02T00:00:00Z"),
        labeled("kind/bug", CMD_BOT, "2025-01-02T00:01:00Z"),
    ]
    assert attribute_labels(pr(*nodes), BOTS)["kind/bug"].kind == "bot"


def test_command_must_be_own_line_and_exact_value():
    nodes = [
        comment("please use /kind bug here", HUMAN, "2025-01-02T00:00:00Z"),
        comment("/kind bugfix", HUMAN, "2025-01-02T00:00:00Z"),
        labeled("kind/bug", CMD_BOT, "2025-01-02T00:01:00Z"),
    ]
    assert attribute_labels(pr(*nodes), BOTS)["kind/bug"].kind == "bot"


def test_ghost_actor_is_unknown_and_after_merge_flag():
    a = attribute_labels(pr(labeled("bug", None, "2025-02-01T00:00:00Z")), BOTS)["bug"]
    assert a.kind == "unknown" and a.after_merge and not is_human_applied(a)
