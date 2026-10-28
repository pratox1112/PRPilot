"""Stage B: everything we need about each PR from GraphQL, in batched, cached queries.

Per PR, in one query: the open-time fields (title/body edit history, renames, force pushes,
commits — see open_time.py), label events with actors, review requests with who asked whom,
submitted reviews, closing issues, issue comments (for slash commands), changed files, and
metadata. Ten PRs per query keeps responses within GitHub's size and timeout limits.
Raw nodes go to data/interim/timeline/<owner>__<repo>.jsonl; nothing is interpreted here.
"""

import json
from pathlib import Path

from prpilot_data.github_api import GitHubGraphQL, GraphQLError
from prpilot_data.open_time import PR_OPEN_TIME_FIELDS

QUERY_VERSION = "timeline-v1"
BATCH = 10

ACTOR = "actor { __typename login }"
PR_TIMELINE_FIELDS = (
    PR_OPEN_TIME_FIELDS
    + f"""
  mergedAt isDraft authorAssociation
  author {{ __typename login }}
  files(first: 100) {{ totalCount nodes {{ path additions deletions }} }}
  closingIssuesReferences(first: 20) {{ totalCount nodes {{ number }} }}
  labelEvents: timelineItems(first: 100, itemTypes: [LABELED_EVENT, UNLABELED_EVENT]) {{
    totalCount
    nodes {{
      __typename
      ... on LabeledEvent {{ createdAt {ACTOR} label {{ name }} }}
      ... on UnlabeledEvent {{ createdAt {ACTOR} label {{ name }} }}
    }}
  }}
  reviewRequests: timelineItems(first: 50, itemTypes: [REVIEW_REQUESTED_EVENT]) {{
    totalCount
    nodes {{
      ... on ReviewRequestedEvent {{
        createdAt {ACTOR}
        requestedReviewer {{ __typename ... on User {{ login }} ... on Team {{ slug }} }}
      }}
    }}
  }}
  reviews(first: 50) {{ totalCount nodes {{ submittedAt state author {{ __typename login }} }} }}
  comments(first: 50) {{
    totalCount
    nodes {{ createdAt body author {{ __typename login }} }}
  }}"""
)


def batch_query(numbers: list[int]) -> str:
    aliases = "\n".join(
        f"  pr{n}: pullRequest(number: {n}) {{{PR_TIMELINE_FIELDS}\n  }}" for n in numbers
    )
    return f"""query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
{aliases}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""


def read_index(path: Path) -> list[int]:
    return [json.loads(line)["number"] for line in path.read_text(encoding="utf-8").splitlines()]


def _query_chunk(gh: GitHubGraphQL, owner: str, name: str, chunk: list[int]):
    """Nodes for a chunk. If GitHub rejects the whole batch (e.g. a server-side timeout on a
    huge PR), retry one PR at a time; PRs that still fail are returned separately."""
    variables = {"owner": owner, "name": name}
    try:
        repo_data = gh.query(batch_query(chunk), variables)["repository"]
        return {n: repo_data.get(f"pr{n}") for n in chunk}, []
    except GraphQLError:
        nodes, failed = {}, []
        for n in chunk:
            try:
                nodes[n] = gh.query(batch_query([n]), variables)["repository"].get(f"pr{n}")
            except GraphQLError as e:
                failed.append({"number": n, "error": str(e)[:300]})
        return nodes, failed


def fetch_repo(gh: GitHubGraphQL, repo: str, numbers: list[int], out_path: Path, log_every=50):
    """Fetch all PRs of one repo; writes one raw node per line.
    Returns (written, missing, failed): missing = PR not found, failed = query errors."""
    owner, name = repo.split("/")
    numbers = sorted(numbers)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = missing = 0
    failed: list[dict] = []
    with out_path.open("w", encoding="utf-8") as f:
        for i in range(0, len(numbers), BATCH):
            chunk = numbers[i : i + BATCH]
            nodes, chunk_failed = _query_chunk(gh, owner, name, chunk)
            failed += chunk_failed
            for node in nodes.values():
                if node is None:
                    missing += 1
                    continue
                f.write(json.dumps({"repo": repo, **node}, ensure_ascii=False) + "\n")
                written += 1
            batch_no = i // BATCH + 1
            if batch_no % log_every == 0:
                print(
                    f"  {repo}: {i + len(chunk)}/{len(numbers)} PRs "
                    f"(network {gh.calls}, cache {gh.cache_hits})"
                )
    return written, missing, failed


def capped_counts(node: dict) -> dict[str, bool]:
    """Which connections hit their page size, so a later stage can flag or refetch."""
    caps = {
        "files": 100,
        "closingIssuesReferences": 20,
        "labelEvents": 100,
        "reviewRequests": 50,
        "reviews": 50,
        "comments": 50,
        "userContentEdits": 100,
        "timelineItems": 100,
        "commits": 100,
    }
    return {k: node[k]["totalCount"] > cap for k, cap in caps.items()}
