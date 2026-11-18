import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from prpilot_core.settings import Settings
from prpilot_core.tracing import langfuse_exporter, setup_tracing


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("TRACING", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)
    yield
    Agent.instrument_all(False)  # instrumentation is process-wide


def run_agent():
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")]))
    return Agent(model).run_sync("hello")


def test_tracing_is_off_by_default():
    exporter = InMemorySpanExporter()
    assert setup_tracing(Settings.from_env(), exporter) is None
    run_agent()
    assert exporter.get_finished_spans() == ()


def test_tracing_on_exports_agent_spans(monkeypatch):
    monkeypatch.setenv("TRACING", "true")
    exporter = InMemorySpanExporter()
    provider = setup_tracing(Settings.from_env(), exporter)
    run_agent()
    provider.force_flush()
    names = [s.name for s in exporter.get_finished_spans()]
    assert any(n.startswith("chat") for n in names) and any("agent" in n for n in names)
    assert provider.resource.attributes["service.name"] == "prpilot"
    provider.shutdown()


def test_tracing_on_without_keys_fails_loudly(monkeypatch):
    monkeypatch.setenv("TRACING", "true")
    with pytest.raises(ValueError, match="LANGFUSE"):
        setup_tracing(Settings.from_env())


def test_langfuse_exporter_endpoint_and_auth(monkeypatch):
    monkeypatch.setattr("prpilot_core.tracing.OTLPSpanExporter", lambda **kw: kw)
    kw = langfuse_exporter("pk", "sk", "https://us.cloud.langfuse.com/")
    assert kw["endpoint"] == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"
    assert kw["headers"] == {"Authorization": "Basic cGs6c2s="}  # base64("pk:sk")
