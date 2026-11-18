"""Replay client leaks nothing from after a PR's open time; live client handles GitHub's
paging, caching, rate limits and errors (against a fake API, no network)."""

import dataclasses
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from prpilot_core.github.live import API, AppAuth, LiveGitHubClient
from prpilot_core.github.replay import OPEN_TIME_FIELDS, NotInReplay, ReplayGitHubClient

pytestmark = pytest.mark.anyio

ROW = {
    "repo": "o/r",
    "number": 5,
    "created_at": "2025-03-01T10:00:00Z",
    "author": "carol",
    "title": "Fix crash",
    "body": "Fixes #12",
    "base_branch": "main",
    "changed_files": ["a.go"],
    "diff": "diff --git a/a.go b/a.go",
    "head_sha": "c" * 40,
    # everything below happened after the PR opened and must never come out
    "merged_at": "2025-03-04T10:00:00Z",
    "labels": ["kind/bug", "approved-later"],
    "reviewers": ["dave"],
    "label_attribution": [{"label": "approved-later", "applied_by": "dave"}],
    "issues": [12, 99],
    "reverted": True,
    "primary_type": "bug",
}
CAND = {"repo": "o/r", "number": 5, "labels": ["kind/bug", "kind/feature"],
        "issues": [{"number": 12, "title": "Crash on start"}]}  # fmt: skip


async def test_replay_returns_only_open_time_facts():
    gh = ReplayGitHubClient([ROW], [CAND])
    pr = await gh.get_pr("o/r", 5)
    assert dataclasses.asdict(pr) == {
        "repo": "o/r", "number": 5, "title": "Fix crash", "body": "Fixes #12",
        "author": "carol", "base_branch": "main", "head_sha": "c" * 40,
        "created_at": "2025-03-01T10:00:00Z",
    }  # fmt: skip
    # labels are the open-time label list, not the labels people applied later
    assert await gh.get_repo_labels("o/r", 5) == ["kind/bug", "kind/feature"]
    assert [i.number for i in await gh.search_issues("o/r", 5)] == [12]
    # nothing later is even stored
    stored = gh._prs[("o/r", 5)]
    assert set(stored) == set(OPEN_TIME_FIELDS)
    blob = json.dumps([dataclasses.asdict(pr), stored])
    for later in ("approved-later", "dave", "2025-03-04", "merged_at", "reverted"):
        assert later not in blob


async def test_replay_unknown_pr():
    with pytest.raises(NotInReplay):
        await ReplayGitHubClient([ROW], [CAND]).get_pr("o/r", 6)


def fake(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def no_sleep(_):
    pass


async def test_live_reads_pr_files_pages_and_issues():
    def handler(req):
        path = req.url.path
        if path.endswith("/pulls/5"):
            return httpx.Response(200, json={
                "title": "T", "body": None, "user": {"login": "carol"}, "base": {"ref": "main"},
                "head": {"sha": "d" * 40}, "created_at": "2026-01-01T00:00:00Z"})  # fmt: skip
        if path.endswith("/pulls/5/files"):
            page = int(req.url.params["page"])
            n = 100 if page == 1 else 3
            return httpx.Response(200, json=[{"filename": f"f{page}_{i}"} for i in range(n)])
        if path.endswith("/issues"):
            return httpx.Response(200, json=[
                {"number": 1, "title": "bug", "body": "x"},
                {"number": 2, "title": "a PR", "pull_request": {}}])  # fmt: skip
        return httpx.Response(404)

    gh = LiveGitHubClient(token="t", http=fake(handler), sleep=no_sleep)
    pr = await gh.get_pr("o/r", 5)
    assert pr.author == "carol" and pr.body == "" and pr.head_sha == "d" * 40
    assert len(await gh.list_changed_files("o/r", 5)) == 103
    assert [i.number for i in await gh.search_issues("o/r", 5)] == [1]  # PRs dropped


async def test_live_etag_cache_serves_304():
    calls = []

    def handler(req):
        calls.append(req.headers.get("If-None-Match"))
        if req.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json=[{"name": "kind/bug"}], headers={"ETag": '"v1"'})

    gh = LiveGitHubClient(token="t", http=fake(handler), sleep=no_sleep)
    assert await gh.get_repo_labels("o/r", 5) == ["kind/bug"]
    assert await gh.get_repo_labels("o/r", 5) == ["kind/bug"]
    assert calls == [None, '"v1"']


async def test_live_waits_on_rate_limit_and_retries_5xx():
    responses = [
        httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "Retry-After": "7"}),
        httpx.Response(502),
        httpx.Response(200, json={"id": 1, "html_url": "u"}),
    ]
    waits = []

    async def sleep(s):
        waits.append(s)

    gh = LiveGitHubClient(token="t", http=fake(lambda req: responses.pop(0)), sleep=sleep)
    out = await gh.post_comment("o/r", 5, "hi")
    assert out == {"comment_id": 1, "url": "u"} and waits == [
        8.0,
        2,
    ]  # Retry-After + 1, then backoff


async def test_live_gives_up_after_retries():
    gh = LiveGitHubClient(token="t", http=fake(lambda req: httpx.Response(500)), sleep=no_sleep)
    with pytest.raises(httpx.HTTPStatusError):
        await gh.get_pr("o/r", 5)


async def test_live_write_payloads():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content)))
        if req.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": "kind/bug"}])
        return httpx.Response(201, json={"requested_reviewers": [{"login": "alice"}]})

    gh = LiveGitHubClient(token="t", http=fake(handler), sleep=no_sleep)
    assert await gh.add_labels("o/r", 5, ["kind/bug"]) == {"labels": ["kind/bug"]}
    assert await gh.request_reviewers("o/r", 5, ["alice"]) == {"requested": ["alice"]}
    assert seen == [
        ("POST", "/repos/o/r/issues/5/labels", {"labels": ["kind/bug"]}),
        ("POST", "/repos/o/r/pulls/5/requested_reviewers", {"reviewers": ["alice"]}),
    ]


async def test_app_auth_signs_a_jwt_and_caches_the_installation_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()  # fmt: skip
    minted = []

    def handler(req):
        if req.url.path == "/app/installations/42/access_tokens":
            claims = jwt.decode(req.headers["Authorization"].split()[1], key.public_key(),
                                algorithms=["RS256"])  # fmt: skip
            minted.append(claims["iss"])
            return httpx.Response(201, json={"token": "inst", "expires_at": "2099-01-01T00:00:00Z"})
        assert req.headers["Authorization"] == "Bearer inst"
        return httpx.Response(200, json=[])

    gh = LiveGitHubClient(auth=AppAuth("123", pem, 42), http=fake(handler), sleep=no_sleep)
    await gh.get_repo_labels("o/r", 5)
    await gh.get_repo_labels("o/r", 5)
    assert minted == ["123"]  # one token for both calls
    assert API.startswith("https://")
