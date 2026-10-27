import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

from prpilot_core.github.webhooks import sign, verify_signature  # noqa: E402

SECRET = "test-secret"
BODY = b'{"action": "opened"}'


def test_valid_signature():
    assert verify_signature(SECRET, BODY, sign(SECRET, BODY))


@pytest.mark.parametrize(
    "header",
    [None, "", "sha1=abc", "sha256=" + "0" * 64, sign("other-secret", BODY)],
)
def test_invalid_signatures(header):
    assert not verify_signature(SECRET, BODY, header)


def test_tampered_body_fails():
    assert not verify_signature(SECRET, BODY + b" ", sign(SECRET, BODY))


def test_empty_secret_never_verifies():
    assert not verify_signature("", BODY, sign("", BODY))


@pytest.fixture
def client(tmp_path, monkeypatch):
    import webhook_receiver

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(webhook_receiver, "DELIVERIES_DIR", tmp_path / "deliveries")
    monkeypatch.setattr(webhook_receiver, "write_result", lambda *a, **k: None)
    webhook_receiver.seen.clear()
    return TestClient(webhook_receiver.app)


def post(client, body: bytes, signature: str | None, delivery: str = "d-1"):
    headers = {"X-GitHub-Delivery": delivery, "X-GitHub-Event": "pull_request"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhook", content=body, headers=headers)


def test_receiver_rejects_unsigned_and_bad(client):
    assert post(client, BODY, None).status_code == 401
    assert post(client, BODY, sign("wrong", BODY)).status_code == 401


def test_receiver_accepts_and_flags_redelivery(client, tmp_path):
    body = json.dumps({"action": "opened", "pull_request": {"number": 1}}).encode()
    first = post(client, body, sign(SECRET, body))
    again = post(client, body, sign(SECRET, body))
    assert first.status_code == 202 and first.json()["duplicate"] is False
    assert again.json()["duplicate"] is True
    assert (tmp_path / "deliveries" / "d-1.json").read_bytes() == body
