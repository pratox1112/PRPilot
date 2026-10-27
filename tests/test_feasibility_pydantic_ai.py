"""Offline check of the 0.8 script's per-PR runner, using a fake model and no exporter."""

import json
import sys
from pathlib import Path

from opentelemetry import trace
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "feasibility"))

import pydantic_ai_check as check  # noqa: E402

from prpilot_core.agents.triage import build_triage_agent  # noqa: E402

GOOD = json.dumps(
    {
        "summary": "Returns an empty dict for empty config files.",
        "type": "bug",
        "risk": "low",
        "labels": ["kind/bug"],
        "reviewers": ["alice-dev"],
        "related_issues": [101],
        "rationale": "A None check is added.",
    }
)


def agent_replying(*replies: str):
    calls = []

    def fn(messages, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart(replies[len(calls) - 1])])

    return build_triage_agent(FunctionModel(fn))


def test_run_one_first_attempt_valid():
    r = check.run_one(agent_replying(GOOD), trace.get_tracer("test"), check.SAMPLES[0])
    assert r["first_attempt"]["valid_static"] and r["first_attempt"]["valid_with_repo_rules"]
    assert r["final_with_retries"]["valid"]
    assert r["retries_used"] == 0
    assert r["usage"]["requests"] == 1


def test_run_one_rescued_by_retry_counts_as_model_failure():
    fenced = "```json\n{}\n```"
    r = check.run_one(agent_replying(fenced, GOOD), trace.get_tracer("t"), check.SAMPLES[0])
    assert not r["first_attempt"]["valid_static"]
    assert r["final_with_retries"]["valid"]
    assert r["retries_used"] == 1


def test_run_one_exhausted_retries():
    r = check.run_one(agent_replying("x", "x", "x"), trace.get_tracer("t"), check.SAMPLES[0])
    assert not r["final_with_retries"]["valid"]
    assert r["usage"] is None
