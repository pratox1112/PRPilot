"""The real GitHub REST API: reads for the pipeline, writes for the executor only.

- Auth: as the GitHub App (a JWT signed with the app's private key, exchanged for an
  installation token, refreshed before it expires), so the bot's labels and comments show
  as the app, not as a person. A personal token can be passed instead for local tests.
- Rate limits: when the quota is used up (403/429 with X-RateLimit-Remaining: 0, or a
  Retry-After), it waits until the reset (capped) instead of retrying blindly; 5xx and
  network errors are retried with exponential backoff.
- Cache: GET responses are kept with their ETag and revalidated with If-None-Match; a 304
  does not count against the rate limit.
Write methods (`add_labels`, `request_reviewers`, `post_comment`) are what the executor's
GitHubWriter protocol calls; nothing registers them as model tools.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import jwt

from prpilot_core.github.client import Issue, PRInfo

API = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_FILES = 3000  # GitHub's own cap on the files endpoint
MAX_RETRIES = 3
MAX_RATE_WAIT_S = 120.0


@dataclass
class AppAuth:
    """Installation token for one GitHub App installation, refreshed before expiry."""

    app_id: str
    private_key: str
    installation_id: int
    _token: str | None = None
    _expires: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def app_jwt(self, now: float | None = None) -> str:
        now = int(now or time.time())
        claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": self.app_id}
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    async def token(self, http: httpx.AsyncClient) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            r = await http.post(
                f"{API}/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {self.app_jwt()}",
                    "Accept": "application/vnd.github+json",
                },  # fmt: skip
            )
            r.raise_for_status()
            data = r.json()
            self._token = data["token"]
            exp = data.get("expires_at")
            self._expires = (
                datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
                if exp
                else time.time() + 3000
            )
            return self._token


async def installation_for(app_id: str, private_key: str, repo: str,
                           http: httpx.AsyncClient) -> int:  # fmt: skip
    """The GitHub App's installation id on a repo (the app must be installed there)."""
    auth = AppAuth(app_id, private_key, 0)
    r = await http.get(f"{API}/repos/{repo}/installation",
                       headers={"Authorization": f"Bearer {auth.app_jwt()}",
                                "Accept": "application/vnd.github+json"})  # fmt: skip
    r.raise_for_status()
    return int(r.json()["id"])


class AppWriter:
    """The executor's GitHubWriter: writes as the GitHub App, one installation per repo."""

    def __init__(self, app_id: str, private_key: str, http: httpx.AsyncClient | None = None):
        self.app_id, self.private_key = app_id, private_key
        self.http = http or httpx.AsyncClient(timeout=30)
        self._clients: dict[str, LiveGitHubClient] = {}

    async def _client(self, repo: str) -> "LiveGitHubClient":
        if repo not in self._clients:
            inst = await installation_for(self.app_id, self.private_key, repo, self.http)
            self._clients[repo] = LiveGitHubClient(
                auth=AppAuth(self.app_id, self.private_key, inst), http=self.http
            )
        return self._clients[repo]

    async def add_labels(self, repo: str, pr_number: int, labels: list[str]) -> dict:
        return await (await self._client(repo)).add_labels(repo, pr_number, labels)

    async def request_reviewers(self, repo: str, pr_number: int, logins: list[str]) -> dict:
        return await (await self._client(repo)).request_reviewers(repo, pr_number, logins)

    async def post_comment(self, repo: str, pr_number: int, body: str) -> dict:
        return await (await self._client(repo)).post_comment(repo, pr_number, body)


class LiveGitHubClient:
    def __init__(
        self,
        auth: AppAuth | None = None,
        token: str | None = None,
        http: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
    ):
        if not auth and not token:
            raise ValueError("need a GitHub App installation (auth) or a token")
        self.auth, self._static = auth, token
        self.http = http or httpx.AsyncClient(timeout=30)
        self._etags: dict[str, tuple[str, object]] = {}
        self._sleep = sleep

    async def _headers(self, accept: str) -> dict:
        tok = await self.auth.token(self.http) if self.auth else self._static
        return {"Authorization": f"Bearer {tok}", "Accept": accept,
                "X-GitHub-Api-Version": API_VERSION}  # fmt: skip

    async def _request(self, method: str, url: str, accept="application/vnd.github+json",
                       json=None, params=None) -> httpx.Response:  # fmt: skip
        key = f"{url}?{sorted((params or {}).items())}|{accept}"
        for attempt in range(MAX_RETRIES + 1):
            headers = await self._headers(accept)
            if method == "GET" and key in self._etags:
                headers["If-None-Match"] = self._etags[key][0]
            try:
                r = await self.http.request(method, url, headers=headers, json=json, params=params)
            except httpx.TransportError:
                if attempt == MAX_RETRIES:
                    raise
                await self._sleep(2**attempt)
                continue
            if r.status_code == 304 and key in self._etags:
                return self._etags[key][1]
            if r.status_code in (403, 429) and (
                r.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in r.headers
            ):
                if attempt == MAX_RETRIES:
                    r.raise_for_status()
                wait = float(r.headers.get("Retry-After") or 0) or max(
                    0.0, float(r.headers.get("X-RateLimit-Reset", time.time())) - time.time()
                )
                await self._sleep(min(wait + 1, MAX_RATE_WAIT_S))
                continue
            if r.status_code >= 500 and attempt < MAX_RETRIES:
                await self._sleep(2**attempt)
                continue
            r.raise_for_status()
            if method == "GET" and "ETag" in r.headers:
                self._etags[key] = (r.headers["ETag"], r)
            return r
        raise RuntimeError("unreachable")

    async def _pages(self, url: str, params: dict, limit: int) -> list:
        out, page = [], 1
        while len(out) < limit:
            r = await self._request("GET", url, params={**params, "per_page": 100, "page": page})
            batch = r.json()
            out += batch
            if len(batch) < 100:
                break
            page += 1
        return out[:limit]

    # --- reads (GitHubClient) ---
    async def get_pr(self, repo: str, number: int) -> PRInfo:
        d = (await self._request("GET", f"{API}/repos/{repo}/pulls/{number}")).json()
        return PRInfo(repo, number, d["title"], d.get("body") or "", d["user"]["login"],
                      d["base"]["ref"], d["head"]["sha"], d["created_at"])  # fmt: skip

    async def get_diff(self, repo: str, number: int) -> str:
        r = await self._request(
            "GET", f"{API}/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff"
        )
        return r.text

    async def list_changed_files(self, repo: str, number: int) -> list[str]:
        files = await self._pages(f"{API}/repos/{repo}/pulls/{number}/files", {}, MAX_FILES)
        return [f["filename"] for f in files]

    async def get_repo_labels(self, repo: str, number: int) -> list[str]:
        return [lab["name"] for lab in await self._pages(f"{API}/repos/{repo}/labels", {}, 1000)]

    async def search_issues(self, repo: str, number: int) -> list[Issue]:
        items = await self._pages(f"{API}/repos/{repo}/issues", {"state": "open"}, 500)
        return [Issue(i["number"], i["title"], i.get("body") or "")
                for i in items if "pull_request" not in i]  # fmt: skip

    # --- writes (executor's GitHubWriter only) ---
    async def add_labels(self, repo: str, pr_number: int, labels: list[str]) -> dict:
        r = await self._request("POST", f"{API}/repos/{repo}/issues/{pr_number}/labels",
                                json={"labels": labels})  # fmt: skip
        return {"labels": [lab["name"] for lab in r.json()]}

    async def request_reviewers(self, repo: str, pr_number: int, logins: list[str]) -> dict:
        r = await self._request(
            "POST", f"{API}/repos/{repo}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": logins},
        )  # fmt: skip
        return {"requested": [u["login"] for u in r.json().get("requested_reviewers", [])]}

    async def post_comment(self, repo: str, pr_number: int, body: str) -> dict:
        r = await self._request("POST", f"{API}/repos/{repo}/issues/{pr_number}/comments",
                                json={"body": body})  # fmt: skip
        d = r.json()
        return {"comment_id": d["id"], "url": d["html_url"]}
