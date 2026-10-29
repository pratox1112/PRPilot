"""Stage D: ground truth per PR from maintainer actions only (roadmap §4.3, no annotation).

- Labels: human-applied labels still on the PR (labels.py: bot filter, slash-command
  attribution, removed labels dropped). Labels added after the merge are kept and counted.
- Type: those labels mapped through data/config/type_mappings.toml; the primary type is the
  first in `primary_type_priority`. No mapped label means no type (never a default "other").
- Reviewers: users who submitted a review before the merge, plus users whose review was
  requested by a human. Requests made by a bot only count when a human asked for them with a
  `/cc @login` command (kubernetes' k8s-ci-robot); otherwise they are automatic assignment.
  Requests made by the PR author within CODEOWNERS_WINDOW of opening are CODEOWNERS
  auto-requests (GitHub records them with the author as actor) and are excluded. The author
  and bots are never target reviewers.
- Issues: the PR's closing issue references, with the issue's repository (an issue in another
  repo cannot be found by searching this repo's issues), and whether the open-time title or
  body already names the issue (Phase 1 decision 5: recall is reported both ways).
"""

import re
from collections import Counter
from datetime import timedelta

from prpilot_data.github_api import GitHubGraphQL
from prpilot_data.labels import RepoBots, attribute_labels, is_bot, is_human_applied
from prpilot_data.open_time import parse_ts, reconstruct

CODEOWNERS_WINDOW = timedelta(minutes=2)
LINKS_QUERY_VERSION = "issue-links-v1"
LINKS_BATCH = 25


def repo_bots(repos_cfg: dict, repo: str) -> RepoBots:
    cfg = repos_cfg["repos"][repo]
    shared = repos_cfg.get("global", {}).get("bot_logins", [])
    return RepoBots(
        bot_logins=frozenset(shared) | frozenset(cfg.get("bot_logins", [])),
        command_bots=frozenset(cfg.get("command_bots", [])),
    )


def label_view(node: dict) -> dict:
    """The shape labels.py expects: label events and comments in one `timelineItems`."""
    comments = [{"__typename": "IssueComment", **c} for c in node["comments"]["nodes"]]
    return {**node, "timelineItems": {"nodes": node["labelEvents"]["nodes"] + comments}}


def label_truth(node: dict, bots: RepoBots, mapping: dict, priority: list[str]) -> dict:
    attributions = attribute_labels(label_view(node), bots)
    human = sorted(a.label for a in attributions.values() if is_human_applied(a))
    types = sorted({mapping[label] for label in human if label in mapping}, key=priority.index)
    return {
        "labels": human,
        "primary_type": types[0] if types else None,
        "types": types,
        "type_labels": [label for label in human if label in mapping],
        "label_attribution": {
            a.label: {
                "kind": a.kind,
                "applied_by": a.applied_by,
                "applied_at": a.applied_at,
                "after_merge": a.after_merge,
            }
            for a in attributions.values()
        },
    }


def _cc_by_human(node: dict, login: str, before: str, bots: RepoBots) -> str | None:
    pattern = re.compile(rf"^\s*/cc\b[^\n]*@{re.escape(login)}\b", re.IGNORECASE | re.MULTILINE)
    sources = [(node.get("author"), node["createdAt"], node.get("body") or "")]
    sources += [
        (c.get("author"), c["createdAt"], c.get("body") or "") for c in node["comments"]["nodes"]
    ]
    for who, at, text in sources:
        if at <= before and who and not is_bot(who, bots) and pattern.search(text):
            return who.get("login")
    return None


def reviewer_truth(node: dict, bots: RepoBots) -> dict:
    author = (node.get("author") or {}).get("login")
    opened = parse_ts(node["createdAt"])
    merged = node.get("mergedAt") or "9999"
    requested: dict[str, str] = {}
    excluded: Counter = Counter()
    for event in node["reviewRequests"]["nodes"]:
        reviewer = event.get("requestedReviewer")
        if not reviewer or reviewer.get("__typename") != "User" or not reviewer.get("login"):
            excluded["team_or_hidden"] += 1
            continue
        login, actor = reviewer["login"], event.get("actor")
        if login == author or is_bot(reviewer, bots):
            excluded["author_or_bot_reviewer"] += 1
        elif actor is None:
            excluded["unknown_actor"] += 1
        elif is_bot(actor, bots):
            if actor.get("login") in bots.command_bots and _cc_by_human(
                node, login, event["createdAt"], bots
            ):
                requested.setdefault(login, "cc_command")
            else:
                excluded["bot_request"] += 1
        elif actor.get("login") == author and parse_ts(event["createdAt"]) - opened <= (
            CODEOWNERS_WINDOW
        ):
            excluded["codeowners_at_open"] += 1
        else:
            requested.setdefault(login, "human_request")
    reviewed = {
        r["author"]["login"]
        for r in node["reviews"]["nodes"]
        if r.get("author")
        and r["author"].get("login")
        and r["author"]["login"] != author
        and not is_bot(r["author"], bots)
        and r.get("submittedAt")  # pending (never submitted) reviews have none
        and r["submittedAt"] <= merged
    }
    evidence = {login: [why] for login, why in requested.items()}
    for login in reviewed:
        evidence.setdefault(login, []).append("reviewed")
    return {
        "reviewers": sorted(evidence),
        "reviewer_evidence": dict(sorted(evidence.items())),
        "excluded_requests": dict(excluded),
    }


def mentions_issue(text: str, repo: str, number: int) -> bool:
    """Does the text name this issue: `#N`, `owner/repo#N` or its github.com URL?"""
    r = re.escape(repo)
    patterns = [
        rf"(?<![\w/.#-])#{number}(?!\w)",  # not #123, not a hex colour like #12ab34
        rf"(?<![\w.-]){r}#{number}(?!\w)",
        rf"github\.com/{r}/issues/{number}(?!\w)",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def issue_truth(node: dict, links: list[dict]) -> dict:
    snapshot = reconstruct(node)
    open_text = f"{snapshot['title']}\n{snapshot['body']}"
    issues = []
    for link in links:
        issue_repo = link["repository"]["nameWithOwner"]
        issues.append(
            {
                "repo": issue_repo,
                "number": link["number"],
                "same_repo": issue_repo.lower() == node["repo"].lower(),
                "mentioned_at_open": mentions_issue(open_text, issue_repo, link["number"]),
            }
        )
    return {"issues": issues}


def links_query(numbers: list[int]) -> str:
    aliases = "\n".join(
        f"  pr{n}: pullRequest(number: {n}) {{ closingIssuesReferences(first: 50) "
        "{ totalCount nodes { number repository { nameWithOwner } } } }"
        for n in numbers
    )
    return f"""query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
{aliases}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""


def fetch_issue_links(gh: GitHubGraphQL, repo: str, numbers: list[int]) -> dict[int, list[dict]]:
    """Closing issues with their repository, for the PRs that have any (stage B kept only
    the numbers)."""
    owner, name = repo.split("/")
    out = {}
    numbers = sorted(numbers)
    for i in range(0, len(numbers), LINKS_BATCH):
        chunk = numbers[i : i + LINKS_BATCH]
        data = gh.query(links_query(chunk), {"owner": owner, "name": name})["repository"]
        for n in chunk:
            out[n] = data[f"pr{n}"]["closingIssuesReferences"]["nodes"]
    return out


def truth_row(
    node: dict, bots: RepoBots, mapping: dict, priority: list[str], links: list[dict]
) -> dict:
    author = node.get("author")
    return {
        "repo": node["repo"],
        "number": node["number"],
        "created_at": node["createdAt"],
        "merged_at": node.get("mergedAt"),
        "author": (author or {}).get("login"),
        "author_is_bot": is_bot(author, bots),
        "author_association": node.get("authorAssociation"),
        **label_truth(node, bots, mapping, priority),
        **reviewer_truth(node, bots),
        **issue_truth(node, links),
    }


def summarize(rows: list[dict]) -> dict:
    kinds: Counter = Counter()
    after_merge = 0
    excluded: Counter = Counter()
    evidence: Counter = Counter()
    for r in rows:
        for a in r["label_attribution"].values():
            kinds[a["kind"]] += 1
            after_merge += a["after_merge"] and a["kind"] in ("human", "slash_command")
        excluded.update(r["excluded_requests"])
        for whys in r["reviewer_evidence"].values():
            evidence.update(whys)
    issues = [i for r in rows for i in r["issues"]]
    return {
        "prs": len(rows),
        "bot_authored": sum(r["author_is_bot"] for r in rows),
        "with_type": sum(r["primary_type"] is not None for r in rows),
        "primary_type": dict(Counter(r["primary_type"] or "none" for r in rows).most_common()),
        "multi_type": sum(len(r["types"]) > 1 for r in rows),
        "with_human_label": sum(bool(r["labels"]) for r in rows),
        "label_applier_kinds": dict(kinds),
        "human_labels_added_after_merge": after_merge,
        "with_reviewer": sum(bool(r["reviewers"]) for r in rows),
        "reviewers_per_pr_mean": round(sum(len(r["reviewers"]) for r in rows) / len(rows), 2)
        if rows
        else None,
        "reviewer_evidence": dict(evidence),
        "excluded_requests": dict(excluded),
        "with_issue": sum(bool(r["issues"]) for r in rows),
        "issues": len(issues),
        "issues_other_repo": sum(not i["same_repo"] for i in issues),
        "issues_mentioned_at_open": sum(i["mentioned_at_open"] for i in issues),
    }
