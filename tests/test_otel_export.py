"""Tests for the OTel export layer.

Three main lines:
1. without an endpoint / SDK, otel.py is a no-op throughout: no errors, no spans;
2. an agent run maps to a root span and an LLM call to a child span with GenAI semantic convention attributes,
   correctly attached under the root span (trace linkage);
3. the gen_ai span gets token usage filled in (reusing ai_client's existing usage data).

Assertions use InMemorySpanExporter, never a real endpoint.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.platform.observability import otel

# Without the opentelemetry SDK, cases involving the exporter are skipped (the no-op cases don't need the SDK).
_otel_sdk = pytest.importorskip("opentelemetry.sdk")


@pytest.fixture
def in_memory_exporter():
    """Install InMemorySpanExporter (synchronous export) and reset otel state after the case."""
    exporter = otel.install_test_exporter()
    try:
        yield exporter
    finally:
        exporter.clear()
        otel.reset()


def _fake_openai_response(content: str, prompt_tokens: int, completion_tokens: int):
    """Build a minimal OpenAI ChatCompletion response stub."""
    return SimpleNamespace(
        model="test-model",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


# ---- no-op fallback -----------------------------------------------------------

def test_init_returns_false_and_stays_off_without_endpoint():
    """Without OTEL_EXPORTER_OTLP_ENDPOINT, init_otel returns False and stays off."""
    otel.reset()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        assert otel.init_otel() is False
    assert otel.is_enabled() is False
    otel.reset()


def test_span_apis_are_no_ops_when_off():
    """With OTel off, every span API is a no-op: no errors and no spans."""
    otel.reset()
    assert otel.is_enabled() is False
    # Context managers return None / a no-op handle, both safe to use
    with otel.agent_run_span("daily_report", trace_id="t-1") as span:
        assert span is None
    with otel.llm_span("gpt-x", operation="chat") as handle:
        handle.set_response(model="gpt-x", input_tokens=1, output_tokens=2)  # no error
    # Detached span APIs
    assert otel.capture_context() is None
    s = otel.start_detached_span("x", attributes={"a": 1})
    assert s is None
    otel.set_span_attributes(s, {"b": 2})  # safe with None
    otel.end_span(s)  # safe with None


def test_ai_client_works_without_spans_when_off():
    """With OTel off, ai_client.chat returns normally and produces no spans."""
    otel.reset()
    from src.platform.ai.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("hello", 10, 5)
    with patch.object(
        client.client.chat.completions, "create", AsyncMock(return_value=fake)
    ):
        out = asyncio.run(client.chat("sys", "user"))
    assert out == "hello"
    assert client.total_tokens_used == 15


# ---- span assertions when enabled ---------------------------------------------------

def test_agent_run_maps_to_root_span(in_memory_exporter):
    """One agent run maps to a root span with the agent name and trace_id attributes."""
    with otel.agent_run_span("daily_report", trace_id="trace-123", trigger_source="schedule"):
        pass
    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    root = spans[0]
    assert root.name == "agent.run daily_report"
    assert root.parent is None  # it is the root
    assert root.attributes[otel.ATTR_AGENT_NAME] == "daily_report"
    assert root.attributes[otel.ATTR_TRACE_ID] == "trace-123"
    assert root.attributes[otel.ATTR_TRIGGER_SOURCE] == "schedule"


def test_llm_call_produces_child_span_with_genai_attributes(in_memory_exporter):
    """An LLM call maps to a gen_ai child span with GenAI semantic convention attributes, attached under the root span."""
    from src.platform.ai.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("analysis result", 100, 40)

    async def _call():
        with otel.agent_run_span("daily_report", trace_id="trace-abc"):
            with patch.object(
                client.client.chat.completions, "create", AsyncMock(return_value=fake)
            ):
                return await client.chat("sys", "user")

    out = asyncio.run(_call())

    assert out == "analysis result"
    spans = in_memory_exporter.get_finished_spans()
    # The child span ends first, the root after
    assert len(spans) == 2
    llm = next(s for s in spans if s.name.startswith("chat"))
    root = next(s for s in spans if s.name.startswith("agent.run"))

    # GenAI semantic convention attributes
    assert llm.attributes[otel.GEN_AI_SYSTEM] == "openai"
    assert llm.attributes[otel.GEN_AI_OPERATION_NAME] == "chat"
    assert llm.attributes[otel.GEN_AI_REQUEST_MODEL] == "test-model"
    assert llm.attributes[otel.GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert llm.attributes[otel.GEN_AI_USAGE_OUTPUT_TOKENS] == 40

    # The child span is under the root span (same trace)
    assert llm.parent is not None
    assert llm.parent.span_id == root.context.span_id
    assert llm.context.trace_id == root.context.trace_id


def test_detached_span_attaches_to_captured_parent_context(in_memory_exporter):
    """start_detached_span uses the captured parent context to attach a node span under the root span (simulating cross-thread use)."""
    with otel.agent_run_span("tradingagents", trace_id="ta-1"):
        parent_ctx = otel.capture_context()
    # After the root span ends, the captured context can still build the parent-child link (simulating to_thread)
    span = otel.start_detached_span(
        "tradingagents.stage market_analyst",
        parent_context=parent_ctx,
        attributes={otel.ATTR_TA_STAGE: "market_analyst"},
    )
    otel.end_span(span)

    spans = in_memory_exporter.get_finished_spans()
    root = next(s for s in spans if s.name.startswith("agent.run"))
    stage = next(s for s in spans if s.name.startswith("tradingagents.stage"))
    assert stage.attributes[otel.ATTR_TA_STAGE] == "market_analyst"
    assert stage.parent is not None
    assert stage.parent.span_id == root.context.span_id
