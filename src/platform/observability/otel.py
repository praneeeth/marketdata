"""OpenTelemetry export layer (optional, off by default).

Adds standard OTel export **without changing** the app's own observability (``log_context`` / ``agent_runs`` /
``tradingagents.observability``), so both the in-house and the standard stack
get the evidence. Three bridges:

- One agent run            -> root span (linked by the ``trace_id`` of ``agent_runs``)
- One LLM call             -> gen_ai child span (reuses the token usage ``ai_client`` already has)
- TradingAgents nodes      -> child spans (reuses the node/LLM events from ``observability.py``)

Design principles (production project; incremental and reversible):

1. **No side effects by default**: without ``OTEL_EXPORTER_OTLP_ENDPOINT``, or without the opentelemetry
   SDK, ``init_otel()`` returns False and every span API becomes a no-op: no errors,
   no runtime dependency, no change to existing behaviour.
2. **Thin bridges**: existing instrumentation points get a context manager; they know nothing of OTel details.
3. **Lazy loading**: this module does **not** import opentelemetry at the top; it tries only when ``init_otel()`` is
   called with an endpoint configured, so ``import src.platform.observability.otel`` is always safe and free.

The GenAI semantic conventions (OpenTelemetry Semantic Conventions for Generative AI) let standard APMs such as
Jaeger / Tempo / Langfuse (OTLP) recognise a span as "one model call".
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---- GenAI semantic convention attribute names -------------------------
# Reference: OpenTelemetry Semantic Conventions for Generative AI
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# App-specific attributes (bridge the in-house trace model, so APM data lines up with agent_runs)
ATTR_AGENT_NAME = "panwatch.agent.name"
ATTR_TRACE_ID = "panwatch.trace_id"
ATTR_TRIGGER_SOURCE = "panwatch.trigger_source"
ATTR_TA_STAGE = "panwatch.tradingagents.stage"

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "panwatch")
_INSTRUMENTATION_SCOPE = "panwatch.otel"

# Module-level state (a singleton within one process)
_enabled: bool = False
_initialized: bool = False
_provider: Any = None
_tracer: Any = None


def is_enabled() -> bool:
    """Whether OTel export is on (endpoint configured, SDK available and initialised)."""
    return _enabled


def _import_sdk():
    """Try to import the OTel SDK. Returns None when it isn't installed (graceful fallback)."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        return trace, Resource, TracerProvider, BatchSpanProcessor
    except Exception:  # pragma: no cover - only hit when the SDK isn't installed
        return None


def _build_otlp_exporter():
    """Build the OTLP span exporter.

    HTTP first (``proto/http``, port 4318 by convention), falling back to gRPC (``proto/grpc``, 4317).
    Both read ``OTEL_EXPORTER_OTLP_ENDPOINT`` and the other standard environment variables, so the endpoint
    isn't passed explicitly here; the SDK resolves it by the standard conventions (least surprise).
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter()
    except Exception:
        pass
    try:  # pragma: no cover - depends on the environment
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcOTLPSpanExporter,
        )

        return GrpcOTLPSpanExporter()
    except Exception:
        return None


def init_otel(*, force: bool = False) -> bool:
    """Initialise OTel export from environment variables. Idempotent; returns whether it was enabled.

    Only enabled when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set **and** the opentelemetry SDK and exporter can
    both be imported; if either is missing it silently becomes a no-op (existing deployments are unaffected).
    """
    global _enabled, _initialized, _provider, _tracer

    if _initialized and not force:
        return _enabled

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        # No endpoint configured: off by default, no side effects.
        _initialized = True
        _enabled = False
        return False

    mods = _import_sdk()
    if mods is None:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set (%s) but the opentelemetry SDK isn't installed; "
            "skipping OTel export. Install: pip install -r requirements-otel.txt",
            endpoint,
        )
        _initialized = True
        _enabled = False
        return False

    trace, Resource, TracerProvider, BatchSpanProcessor = mods
    exporter = _build_otlp_exporter()
    if exporter is None:
        logger.warning(
            "The opentelemetry SDK is installed but the OTLP exporter is missing; skipping OTel export. "
            "Install: pip install -r requirements-otel.txt"
        )
        _initialized = True
        _enabled = False
        return False

    try:
        resource = Resource.create({"service.name": _SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        # Set as the global provider (for context propagation); spans are still created with this module's tracer.
        trace.set_tracer_provider(provider)
        _provider = provider
        _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
        _enabled = True
        _initialized = True
        logger.info("OTel export enabled, endpoint=%s service=%s", endpoint, _SERVICE_NAME)
        return True
    except Exception as e:  # pragma: no cover - catch-all for init errors
        logger.warning("OTel init failed; falling back to no-op: %s", e)
        _enabled = False
        _initialized = True
        return False


# ---- For tests: synchronous export with InMemorySpanExporter ------------

def install_test_exporter():
    """Test only: reset and install an InMemorySpanExporter (SimpleSpanProcessor exports synchronously).

    Returns the exporter so tests can assert on ``get_finished_spans()``. Production code must not call this.
    """
    global _enabled, _initialized, _provider, _tracer

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Tests may install it more than once: overwrite this module's provider/tracer directly;
    # the global provider is only set the first time (OTel forbids overriding it and warns on repeats), so it isn't forced.
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _provider = provider
    _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
    _enabled = True
    _initialized = True
    return exporter


def reset() -> None:
    """Reset module state (for test teardown)."""
    global _enabled, _initialized, _provider, _tracer
    _enabled = False
    _initialized = False
    _provider = None
    _tracer = None


# ---- span APIs (all no-ops when disabled) --------------------------------

@contextmanager
def agent_run_span(
    agent_name: str,
    trace_id: str = "",
    trigger_source: str = "",
) -> Iterator[Any]:
    """Root span of one agent run. No-op when disabled (yields None).

    Carries the ``trace_id`` of ``agent_runs`` as a span attribute, so APM data lines up with the runs table.
    """
    if not _enabled or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"agent.run {agent_name}") as span:
        try:
            span.set_attribute(ATTR_AGENT_NAME, agent_name)
            if trace_id:
                span.set_attribute(ATTR_TRACE_ID, trace_id)
            if trigger_source:
                span.set_attribute(ATTR_TRIGGER_SOURCE, trigger_source)
        except Exception:
            pass
        yield span


class _LLMSpan:
    """Thin handle for a gen_ai span: fills in token usage/response model after the call returns."""

    __slots__ = ("_span",)

    def __init__(self, span: Any):
        self._span = span

    def set_response(
        self,
        *,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        if self._span is None:
            return
        try:
            if model:
                self._span.set_attribute(GEN_AI_RESPONSE_MODEL, model)
            if input_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
            if output_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
        except Exception:
            pass


@contextmanager
def llm_span(
    model: str,
    *,
    system: str = "openai",
    operation: str = "chat",
) -> Iterator[_LLMSpan]:
    """gen_ai child span of one LLM call. Yields a no-op handle when disabled.

    The span name follows the GenAI convention ``{operation} {model}``; request attributes are written on entry, and
    response attributes (tokens/response model) are filled in by the caller through the handle once it has the usage.
    """
    if not _enabled or _tracer is None:
        yield _LLMSpan(None)
        return
    span_name = f"{operation} {model}".strip() if model else operation
    with _tracer.start_as_current_span(span_name) as span:
        try:
            span.set_attribute(GEN_AI_SYSTEM, system)
            span.set_attribute(GEN_AI_OPERATION_NAME, operation)
            if model:
                span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        except Exception:
            pass
        yield _LLMSpan(span)


def capture_context() -> Any:
    """Capture the current OTel context (to carry the root span across threads). Returns None when disabled.

    TradingAgents runs synchronously inside ``asyncio.to_thread``, and OTel context doesn't cross threads automatically;
    it must be captured on the async side and passed explicitly as the parent on the worker thread.
    """
    if not _enabled:
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def start_detached_span(
    name: str,
    *,
    parent_context: Any = None,
    attributes: Optional[dict] = None,
) -> Any:
    """Start a "detached" span (not set as current; must be ended by hand). Returns None when disabled.

    For callback-style instrumentation (such as TradingAgents nodes), where start and end are in separate callbacks and may
    run on a worker thread, so a with block can't be used. Pass the result of ``capture_context()`` as the parent
    to attach it under the root span.
    """
    if not _enabled or _tracer is None:
        return None
    try:
        span = _tracer.start_span(name, context=parent_context)
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        return span
    except Exception:
        return None


def set_span_attributes(span: Any, attributes: dict) -> None:
    """Add attributes to a detached span. No-op when span is None."""
    if span is None:
        return
    for k, v in attributes.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass


def end_span(span: Any) -> None:
    """End a detached span. No-op when span is None."""
    if span is None:
        return
    try:
        span.end()
    except Exception:
        pass
