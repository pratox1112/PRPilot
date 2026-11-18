"""Tracing (roadmap §8.1a): Pydantic AI's OpenTelemetry instrumentation, exported to Langfuse.

Off unless TRACING=true. When on, every agent run and model request becomes a span with its
prompt, reply, token usage and latency. Spans only ever see screened text: the pipeline
builds the prompt from the redacted PR after screening, and a quarantined PR never reaches
the model.
"""

import base64

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from prpilot_core.settings import Settings

SERVICE_NAME = "prpilot"


def langfuse_exporter(public_key: str, secret_key: str, host: str) -> OTLPSpanExporter:
    if not (public_key and secret_key and host):
        raise ValueError("TRACING is on but LANGFUSE_PUBLIC_KEY, _SECRET_KEY or _HOST is empty")
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return OTLPSpanExporter(
        endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}"},
    )


def setup_tracing(
    settings: Settings, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Instrument every agent in this process; None (and nothing changed) when tracing is off.
    The caller shuts the provider down on exit so the last spans are sent."""
    if not settings.tracing:
        return None
    if exporter is None:
        exporter = langfuse_exporter(
            settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host
        )
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
    return provider
