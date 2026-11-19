"""Replay: bundles carry open-time data only, the worker serves bundled PRs from them, and a
signed send goes through the api and the worker to a stored run; the measurement helpers."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from prpilot_core.github.replay import ReplayGitHubClient, bundle_record
from prpilot_core.queue import InProcessQueue
from prpilot_core.settings import Settings
from replay import bundle, measure, send
from services.worker import handler
from tests.test_api import AUTH, SECRET, TOKEN, client
from tests.test_github_clients import CAND, ROW
from tests.test_pipeline import FakeGuard, answer, scripted

LATER = ("approved-later", "dave", "2025-03-04", "merged_at", "reverted")


def test_a_bundle_holds_and_serves_only_open_time_facts(tmp_path):
    path = tmp_path / "b.jsonl"
    bundle.write([bundle_record(ROW, CAND)], path)
    text = path.read_text(encoding="utf-8")
    assert not any(later in text for later in LATER)
    gh = ReplayGitHubClient.from_bundle(path)
    assert ("o/r", 5) in gh and ("o/r", 6) not in gh
    direct = ReplayGitHubClient([ROW], [CAND])
    assert gh._prs == direct._prs and gh._labels == direct._labels
    assert gh._issues == direct._issues and gh._reviewers == direct._reviewers


def test_a_bundle_keeps_line_separators_inside_pr_text(tmp_path):
    row = {**ROW, "body": "one two\rthree"}
    path = tmp_path / "b.jsonl"
    bundle.write([bundle_record(row, CAND)], path)
    assert ReplayGitHubClient.from_bundle(path)._prs[("o/r", 5)]["body"] == "one two\rthree"


def test_bundle_picks_keys_in_order_then_sorts_by_opening_time():
    rows = [{**ROW, "number": n, "created_at": f"2025-03-0{9 - n}T00:00:00Z"} for n in (1, 2, 3)]
    cands = [{**CAND, "number": n} for n in (1, 2, 3)]
    picked = bundle.build(rows, cands, keys=[("o/r", 1), ("o/r", 3), ("o/r", 2)], first=2)
    assert [r["pr"]["number"] for r in picked] == [3, 1]  # 3 opened before 1
    assert [r["pr"]["number"] for r in bundle.build(rows, cands)] == [3, 2, 1]
    every = bundle.build(rows, cands, keys=[("o/r", 1), ("o/r", 2), ("o/r", 3)], every=2)
    assert [r["pr"]["number"] for r in every] == [3, 1]
    with pytest.raises(SystemExit):
        bundle.build(rows, cands[:2])


def settings(path) -> Settings:
    s = Settings.from_env()
    return dataclasses.replace(s, sandbox_repos=frozenset({"me/sandbox"}),
                               replay_bundle=str(path), reader_app_id="",
                               model_audience="")  # fmt: skip


@pytest.mark.anyio
async def test_a_signed_send_reaches_a_stored_run_via_the_replay_worker(db, tmp_path,
                                                                      monkeypatch):  # fmt: skip
    path = tmp_path / "b.jsonl"
    bundle.write([bundle_record(ROW, CAND)], path)
    model, calls = scripted(answer(labels=["kind/bug"], related_issues=[12]))
    monkeypatch.setattr(handler, "screening", lambda *a: (FakeGuard(), None))
    monkeypatch.setattr(handler, "OpenAIChatModel", lambda *a, **k: model)
    monkeypatch.setattr("prpilot_core.context._count_tokens", lambda: lambda t: len(t.split()))
    handle = handler.make_handler(settings(path), db)

    async def consume(event):
        await handle(event)

    queue = InProcessQueue(consume)
    queue.start()
    c, _ = client(db, queue=queue)
    pr = bundle_record(ROW, CAND)["pr"]
    admin = {"Authorization": f"Bearer {TOKEN}"}
    try:
        async with c:
            rec = await send.send_one(c, "http://t", SECRET, admin, "t", pr, timeout=5, poll=0.01)
            again = await send.send_one(c, "http://t", SECRET, admin, "t", pr, timeout=5,
                                        poll=0.01)  # fmt: skip
            [d] = (await c.get("/deliveries", headers=AUTH)).json()
    finally:
        await queue.stop()
    # o/r is not a sandbox repo: it is triaged only because the bundle holds it
    assert rec["http_status"] == 202 and rec["outcome"] == "proposed" and len(calls) == 1
    assert rec["final"]["head_sha"] == ROW["head_sha"] and rec["final"]["triage"]["requests"] == 1
    assert "output" not in rec["final"]["triage"]
    assert again["answer"] == {"status": "duplicate"} and again["outcome"] == "proposed"
    assert d["delivery_id"] == rec["delivery_id"] == send.delivery_id("t", "o/r", 5)


@pytest.mark.anyio
async def test_the_replay_worker_still_refuses_other_repos(db, tmp_path, monkeypatch):
    path = tmp_path / "b.jsonl"
    bundle.write([bundle_record(ROW, CAND)], path)
    monkeypatch.setattr(handler, "screening", lambda *a: pytest.fail("must not screen"))
    handle = handler.make_handler(settings(path), db)
    assert await handle({"repo": "o/r", "number": 6, "installation_id": None}) is False
    assert await handle({"repo": "other/repo", "number": 5, "installation_id": None}) is False


def test_final_states_and_delivery_ids():
    assert send.is_final({"state": "proposed"}) and send.is_final({"state": "quarantined"})
    assert not send.is_final({"state": "failed", "triage": {}})  # Pub/Sub delivers it again
    assert send.is_final({"state": "failed", "triage": {"requests": 3}})
    assert not send.is_final({"state": "screened"})
    assert send.delivery_id("a", "o/r", 5) == send.delivery_id("a", "o/r", 5)
    assert send.delivery_id("a", "o/r", 5) != send.delivery_id("b", "o/r", 5)
    body = send.payload(bundle_record(ROW, CAND)["pr"])
    assert body["pull_request"]["head"]["sha"] == ROW["head_sha"]
    assert "body" not in json.dumps(body) and not any(x in json.dumps(body) for x in LATER)


def test_outcomes_latency_and_prices():
    run = {
        "state": "proposed",
        "created_at": "2026-11-18T10:00:05Z",
        "updated_at": "2026-11-18T10:00:35Z",
        "triage": {"requests": 2, "valid_after_retries": True, "partly_filtered": False},
    }
    sends = [{"delivery_id": "a", "outcome": "proposed", "final": run},
             {"delivery_id": "b", "outcome": "quarantined",
              "final": {**run, "triage": None, "updated_at": "2026-11-18T10:00:10Z"}},
             {"delivery_id": "c", "outcome": "unfinished", "final": None}]  # fmt: skip
    out = measure.outcomes(sends, {"a": "2026-11-18T10:00:00Z", "b": "2026-11-18T10:00:00Z"})
    assert out["outcomes"] == {"proposed": 1, "quarantined": 1, "unfinished": 1}
    assert out["triaged"] == 1 and out["valid_after_retries"] == 1
    assert out["webhook_to_stored"] == {"n": 2, "p50_s": 10.0, "p95_s": 35.0, "max_s": 35.0}
    assert measure.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert measure.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert measure.gib("8Gi") == 8 and measure.gib("512Mi") == 0.5
    sku = {"pricingInfo": [{"pricingExpression": {"usageUnit": "s", "tieredRates": [
        {"startUsageAmount": 0, "unitPrice": {"units": "0", "nanos": 0}},
        {"startUsageAmount": 180000, "unitPrice": {"units": "0", "nanos": 24000}}]}}]}  # fmt: skip
    assert measure.list_price(sku) == (0.000024, "s")


def test_metric_sums_page_and_group():
    pages = iter([
        {"timeSeries": [{"metric": {"labels": {}},
                         "resource": {"labels": {"service_name": "prpilot-worker"}},
                         "points": [{"value": {"doubleValue": 30.5}}]}], "nextPageToken": "x"},
        {"timeSeries": [{"metric": {"labels": {}},
                         "resource": {"labels": {"service_name": "prpilot-model"}},
                         "points": [{"value": {"int64Value": "20"}},
                                    {"value": {"int64Value": "2"}}]}]},
    ])  # fmt: skip
    g = measure.Google("tok")
    reply = httpx.MockTransport(lambda r: httpx.Response(200, json=next(pages)))
    g.http = httpx.Client(transport=reply)
    t = datetime(2026, 11, 18, tzinfo=UTC)
    sums = g.metric_sum("m", t, t + timedelta(minutes=5), group_by="resource.labels.service_name")
    assert sums == {"prpilot-worker": 30.5, "prpilot-model": 22.0}
