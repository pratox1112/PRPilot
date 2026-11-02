"""Spot check (§4.6): random cleaned PRs with their targets and who applied each label, for the
owner to compare against the PR on GitHub. Pipeline debugging, not annotation: nothing read
here becomes a label.

Samples train and val only, so the test split stays unseen before it is frozen. The same
number of PRs per repo, so each repo's bot and CODEOWNERS rules get checked.
"""

import random

BODY_CHARS = 400
FILES_SHOWN = 8


def sample(rows: list[dict], n: int, seed: int = 0) -> list[dict]:
    pool = [r for r in rows if r["split"] in ("train", "val")]
    repos = sorted({r["repo"] for r in pool})
    rng = random.Random(seed)
    picked = []
    for i, repo in enumerate(repos):
        share = n // len(repos) + (i < n % len(repos))
        mine = sorted((r for r in pool if r["repo"] == repo), key=lambda r: r["number"])
        picked += rng.sample(mine, min(share, len(mine)))
    return sorted(picked, key=lambda r: (r["repo"], r["number"]))


def render(r: dict, i: int) -> str:
    url = f"https://github.com/{r['repo']}/pull/{r['number']}"
    body = (r["body"] or "").strip().replace("\r\n", "\n")
    if len(body) > BODY_CHARS:
        body = body[:BODY_CHARS] + " [...]"
    files = r["changed_files"]
    shown = ", ".join(files[:FILES_SHOWN]) + (
        f" (+{len(files) - FILES_SHOWN})" if len(files) > FILES_SHOWN else ""
    )
    lines = [
        f"## {i}. {r['repo']}#{r['number']} ({r['split']})",
        url,
        f"- opened {r['created_at']}, merged {r['merged_at']}; author {r['author']}"
        f"{' (bot)' if r['author_is_bot'] else ''}, {r['author_association']}",
        f"- title at open: {r['title']}",
        f"- diff: {r['diff_source']} (head {r['head_source']}), {len(files)} files"
        f"{', flagged' if r['flagged'] else ''}: {shown or '-'}",
        "- body at open (slash commands removed):",
        "",
        "  > " + body.replace("\n", "\n  > ") if body else "  > (empty)",
        "",
        f"- **type:** {r['primary_type'] or 'none'} (all: {', '.join(r['types']) or '-'})",
        "- labels and who applied them (only human / slash_command count as targets):",
    ]
    for a in sorted(r["label_attribution"], key=lambda a: a["applied_at"] or ""):
        when = "after merge" if a["after_merge"] else "before merge"
        kept = "TARGET" if a["label"] in r["labels"] else "ignored"
        lines.append(
            f"  - `{a['label']}`: {a['kind']} by {a['applied_by']} at {a['applied_at']}"
            f" ({when}) -> {kept}"
        )
    if not r["label_attribution"]:
        lines.append("  - (no labels)")
    lines.append("- **reviewers** (why each counts):")
    for e in r["reviewer_evidence"]:
        lines.append(f"  - {e['login']}: {', '.join(e['evidence'])}")
    if not r["reviewer_evidence"]:
        lines.append("  - (none)")
    excluded = ", ".join(f"{e['reason']} {e['count']}" for e in r["excluded_requests"])
    lines.append(f"- review requests excluded: {excluded or '-'}")
    issues = ", ".join(
        f"{x['repo']}#{x['number']}{' (named at open)' if x['mentioned_at_open'] else ''}"
        for x in r["issues"]
    )
    lines.append(f"- **issues:** {issues or '-'}")
    lines.append(
        f"- **reverted:** {'yes, by #' + str(r['reverted_by']) if r['reverted'] else 'no'}"
    )
    lines.append("- check: [ ] labels  [ ] reviewers  [ ] issues  — notes:")
    return "\n".join(lines)


def report(rows: list[dict], seed: int) -> str:
    head = [
        "# Spot check",
        "",
        f"{len(rows)} PRs from train and val (seed {seed}). For each one, open the link and check:",
        "",
        "1. **Labels:** every TARGET label was applied by a human (or by a bot on a human's"
        " slash command, credited to that human); labels marked ignored were applied by bots or"
        " removed.",
        "2. **Reviewers:** each listed reviewer reviewed the PR or was asked by a person; nobody"
        " was only auto-requested (CODEOWNERS, bot assignment).",
        "3. **Issues:** the listed issues are the ones the PR closes.",
        "",
        "Write down anything wrong; that is a pipeline bug to fix, not a label to change.",
        "",
    ]
    return "\n".join(head) + "\n" + "\n\n".join(render(r, i) for i, r in enumerate(rows, 1)) + "\n"
