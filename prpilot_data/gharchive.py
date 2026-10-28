"""Open-time PR snapshots from GH Archive on BigQuery (roadmap §4.2, Phase 1 decision 6).

A `PullRequestEvent` with action `opened` carries the PR as it was when it was opened:
title, body, head and base SHA. That is the open-time source; GraphQL reconstruction is the
cross-check and fallback.

Cost safety: GH Archive tables are not clustered by repo, so a query scans the whole
`payload` column of every table it touches. Every query is dry-run first; a real run needs an
explicit byte budget and is capped with `maximum_bytes_billed`.
"""

from dataclasses import dataclass

SAMPLE_DAYS = ("20230615", "20250616", "20260216")
# Spread over the whole range, before and after GitHub trimmed payloads on 2025-10-07; used to
# validate the GraphQL open-time reconstruction (head SHA everywhere, title/body before trim).
VALIDATION_DAYS = (
    "20230301",
    "20230913",
    "20231205",
    "20240214",
    "20240611",
    "20241015",
    "20250205",
    "20250415",
    "20250903",
    "20251015",
    "20251203",
    "20260120",
    "20260310",
)
DAY_SETS = {"sample": SAMPLE_DAYS, "validation": VALIDATION_DAYS}
FULL_MONTHS = ("202301", "202603")
POST_TRIM_MONTHS = ("202510", "202603")  # small payloads: head SHA only

_FIELDS = """
  created_at AS event_created_at,
  repo.name AS repo,
  actor.login AS actor,
  SAFE_CAST(JSON_VALUE(payload, '$.number') AS INT64) AS number,
  JSON_VALUE(payload, '$.action') AS action,
  JSON_VALUE(payload, '$.pull_request.title') AS title,
  JSON_VALUE(payload, '$.pull_request.body') AS body,
  JSON_VALUE(payload, '$.pull_request.user.login') AS author,
  JSON_VALUE(payload, '$.pull_request.author_association') AS author_association,
  JSON_VALUE(payload, '$.pull_request.head.sha') AS head_sha,
  JSON_VALUE(payload, '$.pull_request.base.sha') AS base_sha,
  JSON_VALUE(payload, '$.pull_request.base.ref') AS base_ref,
  SAFE_CAST(JSON_VALUE(payload, '$.pull_request.draft') AS BOOL) AS draft,
  JSON_VALUE(payload, '$.pull_request.created_at') AS pr_created_at,
  LENGTH(payload) AS payload_chars"""


def opened_events_sql(table: str, suffix_filter: str) -> str:
    """SQL for `opened` PR events of @repos in the GH Archive tables matching the filter.

    The wildcard is `<dataset>.20*`, not `<dataset>.*`: the datasets also hold views (e.g.
    `day.yesterday`), and BigQuery refuses views inside a wildcard. `_TABLE_SUFFIX` is
    therefore the table name without its leading "20".
    """
    return f"""
SELECT{_FIELDS}
FROM `githubarchive.{table}.20*`
WHERE {suffix_filter}
  AND type = 'PullRequestEvent'
  AND repo.name IN UNNEST(@repos)
  AND JSON_VALUE(payload, '$.action') = 'opened'
ORDER BY event_created_at"""


def days_sql(days: tuple[str, ...] = SAMPLE_DAYS) -> str:
    listed = ", ".join(f"'{d[2:]}'" for d in days)
    return opened_events_sql("day", f"_TABLE_SUFFIX IN ({listed})")


def sample_sql() -> str:
    return days_sql(SAMPLE_DAYS)


def months_sql(start: str, end: str) -> str:
    return opened_events_sql("month", f"_TABLE_SUFFIX BETWEEN '{start[2:]}' AND '{end[2:]}'")


def full_sql() -> str:
    return months_sql(*FULL_MONTHS)


def post_trim_sql() -> str:
    return months_sql(*POST_TRIM_MONTHS)


@dataclass(frozen=True)
class Estimate:
    bytes_processed: int

    @property
    def gib(self) -> float:
        return self.bytes_processed / 2**30

    def usd(self, price_per_tib: float = 6.25) -> float:
        """On-demand list price, before the free first TiB per month."""
        return self.bytes_processed / 2**40 * price_per_tib


def completeness(rows: list[dict]) -> dict:
    """Share of opened events whose payload carries each open-time field, per sample day."""
    fields = ("title", "body", "head_sha", "base_sha", "base_ref", "author", "pr_created_at")
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(str(r["event_created_at"])[:10], []).append(r)
    out = {}
    for day, rs in sorted(by_day.items()):
        out[day] = {
            "events": len(rs),
            **{f: round(sum(r.get(f) not in (None, "") for r in rs) / len(rs), 3) for f in fields},
            "median_payload_chars": sorted(r["payload_chars"] for r in rs)[len(rs) // 2],
        }
    return out


def byte_cap(estimate: Estimate, budget_gib: float) -> int:
    """maximum_bytes_billed for a run: the estimate plus 10%, refusing anything over budget."""
    if estimate.gib > budget_gib:
        raise SystemExit(
            f"estimate {estimate.gib:.1f} GiB exceeds the approved budget of {budget_gib} GiB"
        )
    return (estimate.bytes_processed * 11 + 9) // 10  # ceil(1.1 * bytes), exact in integers
