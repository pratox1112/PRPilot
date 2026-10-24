"""Minimal GitHub GraphQL client: read-only, rate-limit aware, every response cached on disk.

Cache key = query version + query text + variables, so a re-run never refetches (§4.2).
"""

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

API_URL = "https://api.github.com/graphql"
MIN_REMAINING = 200


class GraphQLError(RuntimeError):
    pass


class GitHubGraphQL:
    def __init__(self, cache_dir: Path, query_version: str, token: str | None = None):
        token = token or os.getenv("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (put it in .env)")
        self.cache_dir = cache_dir
        self.query_version = query_version
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "User-Agent": "prpilot-feasibility"},
            timeout=60,
        )
        self.calls = 0
        self.cache_hits = 0

    def _cache_path(self, query: str, variables: dict) -> Path:
        key = json.dumps([self.query_version, query, variables], sort_keys=True)
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def query(self, query: str, variables: dict) -> dict:
        path = self._cache_path(query, variables)
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        data = self._post(query, variables)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _post(self, query: str, variables: dict, attempts: int = 6) -> dict:
        for attempt in range(attempts):
            resp = self.client.post(API_URL, json={"query": query, "variables": variables})
            self.calls += 1
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                wait = int(resp.headers.get("retry-after", 0)) or min(2**attempt * 5, 300)
                print(f"  HTTP {resp.status_code}; retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise GraphQLError(json.dumps(body["errors"])[:1000])
            self._respect_rate_limit(body["data"].get("rateLimit"))
            return body["data"]
        raise GraphQLError(f"gave up after {attempts} attempts")

    @staticmethod
    def _respect_rate_limit(rate: dict | None) -> None:
        if not rate or rate["remaining"] > MIN_REMAINING:
            return
        reset = datetime.fromisoformat(rate["resetAt"].replace("Z", "+00:00"))
        wait = max((reset - datetime.now(UTC)).total_seconds(), 0) + 5
        print(f"  rate limit low ({rate['remaining']} left); sleeping {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
