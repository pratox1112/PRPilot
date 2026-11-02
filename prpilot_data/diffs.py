"""Stage C: each PR's diff as it was when the PR was opened (roadmap §4.4).

The diff GitHub shows today is the final one. The open-time diff is
`compare/<base at open>...<head at open>`:
- head: the GH Archive `opened` payload where we have it (2025-10..2026-03), otherwise the
  GraphQL reconstruction from open_time.py, otherwise the final head (flagged);
- base: GH Archive's `base_sha` where we have it, otherwise the base branch's tip when the PR
  was opened, read off the branch's first-parent history. It cannot be today's branch:
  repos that merge with merge commits make the open-time head an ancestor of the branch, and
  `compare` against it would return an empty diff.
If the head is unresolved or the compare fails (commit gone, diff too large), the fallback is
the PR's final diff, flagged; every row says which one it got. Not the final head against the
base at open: PRs rebased before merging would then include every branch change in between
(seen in the trial: 10-60 MB "diffs" for cilium PRs).
The full diff is reduced to the 6,000-token budget here; the file list comes from the full one.
"""

import json
import random
import statistics
import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from itertools import accumulate
from pathlib import Path

import httpx

from prpilot_data.github_api import GitHubGraphQL, GraphQLError
from prpilot_data.inputs import changed_files, fit_diff_to_tokens
from prpilot_data.open_time import parse_ts, reconstruct
from prpilot_data.records import read_jsonl

QUERY_VERSION = "diffs-v1"
DIFF_TOKENS = 6000
DIFF_ACCEPT = "application/vnd.github.diff"
HISTORY_SINCE = "2022-11-01T00:00:00Z"  # two months before the data range starts
FETCH_ERRORS = (httpx.HTTPStatusError, GraphQLError)

HISTORY_QUERY = """query($owner: String!, $name: String!, $ref: String!, $since: GitTimestamp!,
                         $after: String) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $ref) {
      target {
        ... on Commit {
          history(first: 100, after: $after, since: $since) {
            pageInfo { hasNextPage endCursor }
            nodes { oid committedDate parents(first: 1) { nodes { oid } } }
          }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}"""


def fetch_history(gh: GitHubGraphQL, repo: str, branch: str) -> list[dict]:
    """Every commit reachable from the branch since HISTORY_SINCE; the first node is the tip."""
    owner, name = repo.split("/")
    nodes, after, pages = [], None, 0
    while True:
        variables = {
            "owner": owner,
            "name": name,
            "ref": f"refs/heads/{branch}",
            "since": HISTORY_SINCE,
            "after": after,
        }
        history = gh.query(HISTORY_QUERY, variables)["repository"]["ref"]["target"]["history"]
        nodes += history["nodes"]
        pages += 1
        if pages % 50 == 0:
            print(f"  {repo}: {len(nodes)} commits of {branch} history", file=sys.stderr)
        if not history["pageInfo"]["hasNextPage"]:
            return nodes
        after = history["pageInfo"]["endCursor"]


def first_parent_chain(nodes: list[dict]) -> list[tuple[str, str]]:
    """(committedDate, oid) along first parents from the tip, oldest first. These are the
    commits the branch itself pointed at; commits that arrived inside a merge are skipped."""
    by_oid = {n["oid"]: n for n in nodes}
    chain, current = [], nodes[0] if nodes else None
    while current:
        chain.append((current["committedDate"], current["oid"]))
        parents = current["parents"]["nodes"]
        current = by_oid.get(parents[0]["oid"]) if parents else None
    return chain[::-1]


class BaseIndex:
    """Branch tip at a moment in time. Dates on a first-parent chain are nearly but not
    strictly increasing (a pushed commit can carry an old date), so this uses the running
    maximum: the answer is the newest commit with no later-dated commit before it."""

    def __init__(self, chain: list[tuple[str, str]]):
        self.oids = [oid for _, oid in chain]
        self.latest = list(accumulate((parse_ts(d) for d, _ in chain), max))

    def at(self, ts: str) -> str | None:
        i = bisect_right(self.latest, parse_ts(ts))
        return self.oids[i - 1] if i else None


def base_branch(nodes: list[dict]) -> str:
    return Counter(n["baseRefName"] for n in nodes).most_common(1)[0][0]


def load_archive(path: Path) -> dict[tuple[str, int], dict]:
    """GH Archive open-time heads by (repo, number); the earliest event wins."""
    out: dict[tuple[str, int], dict] = {}
    for row in sorted(read_jsonl(path), key=lambda r: r["event_created_at"]):
        out.setdefault((row["repo"], row["number"]), row)
    return out


def choose_head(node: dict, archive: dict | None) -> tuple[str, str]:
    if archive and archive.get("head_sha"):
        return archive["head_sha"], "gharchive"
    snapshot = reconstruct(node)
    if snapshot["head_sha"]:
        return snapshot["head_sha"], snapshot["head_method"]
    return node["headRefOid"], "final_head"


def choose_base(node: dict, archive: dict | None, index: BaseIndex) -> tuple[str | None, str]:
    if archive and archive.get("base_sha"):
        return archive["base_sha"], "gharchive"
    base = index.at(node["createdAt"])
    return base, ("first_parent_at_open" if base else "none")


def compare_diff(gh: GitHubGraphQL, repo: str, base: str, head: str) -> str:
    return gh.rest_get(f"/repos/{repo}/compare/{base}...{head}", DIFF_ACCEPT)


def pr_diff(gh: GitHubGraphQL, repo: str, number: int) -> str:
    return gh.rest_get(f"/repos/{repo}/pulls/{number}", DIFF_ACCEPT)


def fetch_open_time_diff(
    gh: GitHubGraphQL, node: dict, archive: dict | None, index: BaseIndex
) -> dict:
    repo = node["repo"]
    head, head_source = choose_head(node, archive)
    base, base_source = choose_base(node, archive, index)
    row = {"head_sha": head, "head_source": head_source, "base_sha": base}
    row["base_source"] = base_source
    errors = []
    if head_source != "final_head" and base:
        try:
            diff = compare_diff(gh, repo, base, head)
            if diff.strip():
                return {**row, "diff_source": "open_time", "errors": errors, "diff": diff}
            errors.append("open_time: empty diff")
        except FETCH_ERRORS as e:
            errors.append(f"open_time: {str(e)[:200]}")
    try:
        diff = pr_diff(gh, repo, node["number"])
        return {**row, "diff_source": "final_pr_diff", "errors": errors, "diff": diff}
    except FETCH_ERRORS as e:
        errors.append(f"final_pr_diff: {str(e)[:200]}")
        return {**row, "diff_source": "unavailable", "errors": errors, "diff": ""}


def diff_row(
    gh: GitHubGraphQL,
    node: dict,
    archive: dict | None,
    index: BaseIndex,
    count_tokens: Callable[[str], int],
) -> dict:
    got = fetch_open_time_diff(gh, node, archive, index)
    diff = got.pop("diff")
    text, truncated, tokens = fit_diff_to_tokens(diff, DIFF_TOKENS, count_tokens)
    return {
        "repo": node["repo"],
        "number": node["number"],
        "created_at": node["createdAt"],
        **got,
        "changed_files": list(changed_files(diff)),
        "diff_chars": len(diff),
        "diff_tokens": tokens,
        "truncated": truncated,
        "diff": text,
    }


def load_done(path: Path) -> set[int]:
    """PR numbers already written. An interrupted run can leave a half-written last line;
    it is dropped (and the file rewritten) so that PR is fetched again."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        lines = [ln for ln in f.read().split("\n") if ln.strip()]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
    if len(rows) != len(lines):
        with path.open("w", encoding="utf-8", newline="") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return {r["number"] for r in rows}


def process_repo(
    gh: GitHubGraphQL,
    nodes: list[dict],
    archive: dict[tuple[str, int], dict],
    index: BaseIndex,
    count_tokens: Callable[[str], int],
    out_path: Path,
    log_every: int = 200,
) -> int:
    """Append one row per PR not yet in out_path (resumable). Returns rows written now."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out_path)
    todo = sorted((n for n in nodes if n["number"] not in done), key=lambda n: n["number"])
    with out_path.open("a", encoding="utf-8", newline="") as f:
        for i, node in enumerate(todo, 1):
            row = diff_row(
                gh, node, archive.get((node["repo"], node["number"])), index, count_tokens
            )
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if i % log_every == 0:
                print(
                    f"  {node['repo']}: {len(done) + i}/{len(nodes)} PRs "
                    f"(network {gh.calls}, cache {gh.cache_hits})"
                )
    return len(todo)


def summarize(rows: list[dict]) -> dict:
    tokens = [r["diff_tokens"] for r in rows if r["diff_tokens"] is not None]
    return {
        "prs": len(rows),
        "diff_source": dict(Counter(r["diff_source"] for r in rows)),
        "head_source": dict(Counter(r["head_source"] for r in rows)),
        "base_source": dict(Counter(r["base_source"] for r in rows)),
        "truncated": sum(r["truncated"] for r in rows),
        "too_large_to_count": len(rows) - len(tokens),
        "full_diff_tokens_median": statistics.median(tokens) if tokens else None,
    }


def validate(
    gh: GitHubGraphQL,
    nodes: dict[tuple[str, int], dict],
    archive: dict[tuple[str, int], dict],
    indexes: dict[str, BaseIndex],
    sample: int,
    seed: int = 0,
) -> dict:
    """Check the two reconstructions against GH Archive on the PRs where it has the truth.

    Heads: GraphQL reconstruction vs GH Archive head, on every post-trim PR we kept.
    Bases: for a seeded sample, the diff from our first-parent base vs the diff from
    GH Archive's base (same GH Archive head), compared byte for byte."""
    keys = sorted(k for k in archive if k in nodes)
    heads: Counter = Counter()
    for k in keys:
        snapshot = reconstruct(nodes[k])
        agree = snapshot["head_sha"] == archive[k]["head_sha"]
        heads[f"{snapshot['head_method']}:{'agree' if agree else 'differ'}"] += 1
    picked = random.Random(seed).sample(keys, min(sample, len(keys)))
    bases: Counter = Counter()
    differing = []
    for repo, number in picked:
        truth = archive[(repo, number)]
        ours = indexes[repo].at(nodes[(repo, number)]["createdAt"])
        if ours is None:
            bases["no_base"] += 1
            continue
        if ours == truth["base_sha"]:
            bases["same_sha"] += 1
            continue
        try:
            same = compare_diff(gh, repo, ours, truth["head_sha"]) == compare_diff(
                gh, repo, truth["base_sha"], truth["head_sha"]
            )
        except FETCH_ERRORS:
            bases["fetch_error"] += 1
            continue
        bases["same_diff" if same else "different_diff"] += 1
        if not same:
            differing.append({"repo": repo, "number": number})
    agree = bases["same_sha"] + bases["same_diff"]
    checked = agree + bases["different_diff"]
    return {
        "post_trim_prs": len(keys),
        "head_reconstruction": dict(sorted(heads.items())),
        "head_agree": sum(v for k, v in heads.items() if k.endswith(":agree")),
        "base_sample": len(picked),
        "base_outcomes": dict(bases),
        "base_diff_agree": {
            "agree": agree,
            "n": checked,
            "rate": agree / checked if checked else None,
        },
        "base_differing": differing,
    }
