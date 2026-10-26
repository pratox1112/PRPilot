"""Task 0.8: Pydantic AI + llama.cpp + Langfuse feasibility check.

Runs the triage agent against a local llama.cpp server on a few synthetic PRs and
records, per PR:
  - model metric: is the FIRST reply valid against the static schema (no retries,
    no constrained decoding) — hard rule 10;
  - system metric: is the final output valid after validator retries.
Traces go to Langfuse over OpenTelemetry.

Usage (llama.cpp server must be running on --base-url):
    uv run python scripts/feasibility/pydantic_ai_check.py
"""

import argparse
import base64
import os
import sys
import time
from dataclasses import asdict

import httpx
from common import content_hash, run_metadata, write_result
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import ValidationError
from pydantic_ai import (
    Agent,
    InstrumentationSettings,
    UnexpectedModelBehavior,
    capture_run_messages,
)
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from prpilot_core.agents.triage import (
    DEFAULT_OUTPUT_RETRIES,
    PRContext,
    build_triage_agent,
    build_user_prompt,
    parse_static,
    repo_rule_errors,
)
from prpilot_core.deps import TriageDeps

MODEL = {
    "repo": "Qwen/Qwen3-1.7B-GGUF",
    "revision": "90862c4b9d2787eaed51d12237eafdfe7c5f6077",
    "file": "Qwen3-1.7B-Q8_0.gguf",
    "sha256": "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a",
}
SERVER_IMAGE = (
    "ghcr.io/ggml-org/llama.cpp@sha256:"
    "20c4196fc08b459a34ef78aaa05b101333b2fac687e30ec99f37b3c9dc071b44"
)
MODEL_SETTINGS = ModelSettings(
    temperature=0.0,
    seed=0,
    max_tokens=1024,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)

from synthetic_prs import RULES, SAMPLES  # noqa: E402


def setup_langfuse() -> TracerProvider | None:
    load_dotenv()
    pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "").rstrip("/")
    if not (pk and sk and host):
        print("Langfuse keys missing in .env; tracing disabled.", file=sys.stderr)
        return None
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}"},
    )
    provider = TracerProvider(resource=Resource.create({"service.name": "prpilot-feasibility"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
    return provider


def server_info(base_url: str) -> dict:
    root = base_url.removesuffix("/v1")
    props = httpx.get(f"{root}/props", timeout=10).json()
    return {
        "build_info": props.get("build_info"),
        "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx"),
        "model_path": props.get("model_path"),
    }


def first_reply_text(messages) -> str | None:
    for m in messages:
        if isinstance(m, ModelResponse):
            return "".join(p.content for p in m.parts if isinstance(p, TextPart))
    return None


def check_first_reply(text: str | None, deps: TriageDeps) -> dict:
    """Model metric: validate the first reply exactly as produced, once."""
    if text is None:
        return {"valid_static": False, "valid_with_repo_rules": False, "error": "no reply"}
    try:
        out = parse_static(text)
    except ValidationError as e:
        return {"valid_static": False, "valid_with_repo_rules": False, "error": str(e)[:500]}
    errors = repo_rule_errors(out, deps)
    return {"valid_static": True, "valid_with_repo_rules": not errors, "error": "; ".join(errors)}


def run_one(agent, tracer, pr: PRContext) -> dict:
    deps = TriageDeps(rules=RULES, pr_author=pr.author)
    with tracer.start_as_current_span("feasibility.triage_pr") as span:
        trace_id = format(span.get_span_context().trace_id, "032x")
        start = time.perf_counter()
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    build_user_prompt(pr, RULES), deps=deps, model_settings=MODEL_SETTINGS
                )
                final = {"valid": True, "output": result.output.model_dump(mode="json")}
                usage = result.usage
            except UnexpectedModelBehavior as e:
                final = {"valid": False, "error": str(e)[:500]}
                usage = None
        latency_s = time.perf_counter() - start

    first_text = first_reply_text(messages)
    requests = sum(isinstance(m, ModelResponse) for m in messages)
    return {
        "title": pr.title,
        "trace_id": trace_id,
        "latency_s": round(latency_s, 2),
        "model_requests": requests,
        "retries_used": max(requests - 1, 0),
        "first_attempt": {
            **check_first_reply(first_text, deps),
            "contains_think_tag": "<think>" in (first_text or ""),
            "raw_text": first_text,
        },
        "final_with_retries": final,
        "usage": None
        if usage is None
        else {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "requests": usage.requests,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080/v1")
    args = parser.parse_args()
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    provider = setup_langfuse()
    tracer = trace.get_tracer("prpilot.feasibility")
    model = OpenAIChatModel(
        MODEL["file"], provider=OpenAIProvider(base_url=args.base_url, api_key="local")
    )
    agent = build_triage_agent(model, output_retries=DEFAULT_OUTPUT_RETRIES)

    runs = [run_one(agent, tracer, pr) for pr in SAMPLES]
    if provider:
        provider.force_flush()
        provider.shutdown()

    n = len(runs)
    payload = {
        "task": "0.8",
        "meta": run_metadata(["pydantic-ai-slim", "pydantic", "openai", "opentelemetry-sdk"]),
        "dataset": {
            "name": "synthetic_sample_prs",
            "n": n,
            "hash": content_hash([asdict(pr) for pr in SAMPLES]),
        },
        "model": MODEL,
        "server": {"image": SERVER_IMAGE, **server_info(args.base_url)},
        "settings": {
            "output_mode": "text (TextOutput + own JSON parse)",
            "output_retries": DEFAULT_OUTPUT_RETRIES,
            "constrained_decoding": False,
            "model_settings": dict(MODEL_SETTINGS),
        },
        "tracing": {"langfuse": provider is not None},
        "summary": {
            "model_first_attempt_valid_static": sum(
                r["first_attempt"]["valid_static"] for r in runs
            ),
            "model_first_attempt_valid_with_repo_rules": sum(
                r["first_attempt"]["valid_with_repo_rules"] for r in runs
            ),
            "system_valid_with_retries": sum(r["final_with_retries"]["valid"] for r in runs),
            "n": n,
            "note": "Feasibility smoke check on synthetic PRs; not a resume metric.",
        },
        "runs": runs,
    }
    path = write_result("feasibility_pydantic_ai", payload)
    s = payload["summary"]
    print(
        f"first-attempt valid (static): {s['model_first_attempt_valid_static']}/{n}; "
        f"valid after retries (system): {s['system_valid_with_retries']}/{n}"
    )
    print(f"trace ids: {[r['trace_id'] for r in runs]}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
