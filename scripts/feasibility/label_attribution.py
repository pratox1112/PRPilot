"""Who applied each PR label: a human, a bot, or a bot acting on a human slash command.

Implements the ground-truth rules of roadmap §4.3 on one PR's GraphQL timeline:
- the target labels are the ones still applied after the last label event (labels removed
  again are not targets);
- the applier is the actor of the last `LabeledEvent` for that label;
- an actor is a bot if GitHub reports type `Bot`, the login ends in `[bot]`, or the login is
  in a configured bot list;
- if a configured command bot applied the label, it counts as human when a human wrote the
  matching slash command (`/kind bug` for `kind/bug`, or `/label <name>`) in the PR body or
  in a comment before the label event.
"""

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["human", "bot", "slash_command", "unknown"]


@dataclass(frozen=True)
class RepoBots:
    bot_logins: frozenset[str]
    command_bots: frozenset[str]


@dataclass(frozen=True)
class LabelAttribution:
    label: str
    kind: Kind
    applied_by: str | None
    applied_at: str
    after_merge: bool


def is_bot(actor: dict | None, bots: RepoBots) -> bool:
    if not actor:
        return False
    login = actor.get("login") or ""
    return actor.get("__typename") == "Bot" or login.endswith("[bot]") or login in bots.bot_logins


def command_patterns(label: str) -> list[re.Pattern]:
    patterns = [re.compile(rf"^\s*/label\s+{re.escape(label)}\s*$", re.IGNORECASE | re.MULTILINE)]
    if "/" in label:
        prefix, value = label.split("/", 1)
        patterns.append(
            re.compile(
                rf"^\s*/{re.escape(prefix)}\s+{re.escape(value)}\s*$", re.IGNORECASE | re.MULTILINE
            )
        )
    return patterns


def _human_command_author(pr: dict, label: str, before: str, bots: RepoBots) -> str | None:
    patterns = command_patterns(label)
    author = pr.get("author")
    sources = [(author, pr.get("createdAt", ""), pr.get("body") or "")]
    for node in pr["timelineItems"]["nodes"]:
        if node.get("__typename") == "IssueComment":
            sources.append((node.get("author"), node["createdAt"], node.get("body") or ""))
    for who, at, text in sources:
        if at <= before and who and not is_bot(who, bots) and any(p.search(text) for p in patterns):
            return who.get("login")
    return None


def attribute_labels(pr: dict, bots: RepoBots) -> dict[str, LabelAttribution]:
    events = [
        n
        for n in pr["timelineItems"]["nodes"]
        if n.get("__typename") in ("LabeledEvent", "UnlabeledEvent") and n.get("label")
    ]
    events.sort(key=lambda n: n["createdAt"])
    last: dict[str, dict] = {}
    for event in events:
        last[event["label"]["name"]] = event

    merged_at = pr.get("mergedAt") or ""
    result = {}
    for label, event in last.items():
        if event["__typename"] != "LabeledEvent":
            continue
        actor = event.get("actor")
        applied_by = actor.get("login") if actor else None
        if actor is None:
            kind: Kind = "unknown"
        elif is_bot(actor, bots):
            kind = "bot"
            if applied_by in bots.command_bots:
                human = _human_command_author(pr, label, event["createdAt"], bots)
                if human:
                    kind, applied_by = "slash_command", human
        else:
            kind = "human"
        result[label] = LabelAttribution(
            label=label,
            kind=kind,
            applied_by=applied_by,
            applied_at=event["createdAt"],
            after_merge=bool(merged_at) and event["createdAt"] > merged_at,
        )
    return result


def is_human_applied(a: LabelAttribution) -> bool:
    return a.kind in ("human", "slash_command")
