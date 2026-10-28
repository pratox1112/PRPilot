import httpx

from prpilot_data import github_api
from prpilot_data.github_api import GitHubGraphQL


def test_dropped_connection_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(github_api.time, "sleep", lambda s: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"data": {"ok": True, "rateLimit": None}})

    gh = GitHubGraphQL(tmp_path, "v1", token="t")
    gh.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert gh.query("{ ok }", {}) == {"ok": True, "rateLimit": None}
    assert len(attempts) == 2 and gh.calls == 2


def test_cached_answer_needs_no_network(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"data": {"n": 1, "rateLimit": None}})

    gh = GitHubGraphQL(tmp_path, "v1", token="t")
    gh.client = httpx.Client(transport=httpx.MockTransport(handler))
    gh.query("{ n }", {})

    def no_network(request):
        raise AssertionError("should come from cache")

    gh.client = httpx.Client(transport=httpx.MockTransport(no_network))
    assert gh.query("{ n }", {}) == {"n": 1, "rateLimit": None}
    assert gh.cache_hits == 1
