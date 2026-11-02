"""Stage B follow-up: fetch the rest of every connection that came back with a full page.

Stage B asked for one page per connection. For the few PRs where a page that matters for
ground truth or open-time inputs was full, this refetches that connection from the start,
page by page, and replaces its nodes. `files` is skipped: stage C takes the complete file
list from the diff.
"""

from prpilot_data.github_api import GitHubGraphQL
from prpilot_data.timeline import ACTOR

QUERY_VERSION = "complete-v1"

# connection key in the stage B node -> (GraphQL selection with $after, node fields)
CONNECTIONS = {
    "reviews": (
        "reviews(first: 100, after: $after)",
        "submittedAt state author { __typename login }",
    ),
    "comments": (
        "comments(first: 100, after: $after)",
        "createdAt body author { __typename login }",
    ),
    "labelEvents": (
        "timelineItems(first: 100, after: $after, itemTypes: [LABELED_EVENT, UNLABELED_EVENT])",
        f"""__typename
        ... on LabeledEvent {{ createdAt {ACTOR} label {{ name }} }}
        ... on UnlabeledEvent {{ createdAt {ACTOR} label {{ name }} }}""",
    ),
    "reviewRequests": (
        "timelineItems(first: 100, after: $after, itemTypes: [REVIEW_REQUESTED_EVENT])",
        f"""... on ReviewRequestedEvent {{
          createdAt {ACTOR}
          requestedReviewer {{ __typename ... on User {{ login }} ... on Team {{ slug }} }}
        }}""",
    ),
    "timelineItems": (
        "timelineItems(first: 100, after: $after, "
        "itemTypes: [RENAMED_TITLE_EVENT, HEAD_REF_FORCE_PUSHED_EVENT])",
        """__typename
        ... on RenamedTitleEvent { createdAt previousTitle currentTitle }
        ... on HeadRefForcePushedEvent {
          createdAt
          beforeCommit { oid committedDate }
          afterCommit { oid committedDate }
        }""",
    ),
    "commits": ("commits(first: 100, after: $after)", "commit { oid committedDate }"),
}
PAGE_SIZE = {
    "reviews": 50,
    "comments": 50,
    "labelEvents": 100,
    "reviewRequests": 50,
    "timelineItems": 100,
    "commits": 100,
}


def connection_query(key: str) -> str:
    selection, fields = CONNECTIONS[key]
    return f"""query($owner: String!, $name: String!, $number: Int!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      conn: {selection} {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ {fields} }}
      }}
    }}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""


def needs_completion(node: dict) -> list[str]:
    """Connections whose single page was full and that have not been completed yet."""
    done = set(node.get("completed_connections", []))
    return [k for k, size in PAGE_SIZE.items() if k not in done and len(node[k]["nodes"]) >= size]


def fetch_connection(gh: GitHubGraphQL, repo: str, number: int, key: str) -> list[dict]:
    owner, name = repo.split("/")
    query, nodes, after = connection_query(key), [], None
    while True:
        variables = {"owner": owner, "name": name, "number": number, "after": after}
        conn = gh.query(query, variables)["repository"]["pullRequest"]["conn"]
        nodes += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes
        after = conn["pageInfo"]["endCursor"]


def complete_node(gh: GitHubGraphQL, node: dict) -> tuple[dict, list[str]]:
    """Return the node with every full connection replaced by its complete list."""
    keys = needs_completion(node)
    for key in keys:
        node[key] = {**node[key], "nodes": fetch_connection(gh, node["repo"], node["number"], key)}
    if keys:
        node["completed_connections"] = sorted(
            set(node.get("completed_connections", [])) | set(keys)
        )
    return node, keys
