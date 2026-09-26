"""Unit tests for runtime AI model failover (fully mocked; no real requests).

Covers:
- error classification (retry without the parameter / switch models / raise);
- negative-cache cooldown and recovery probes;
- streaming failover (switch before the first token; propagate after it);
- candidate chain building (main model + backups from the DB);
- runs record the model actually used (AgentContext.model_label reflects used_model_label).
"""

import asyncio

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.ai import ai_failover as m
from src.platform.ai.ai_failover import (
    ERR_FATAL,
    ERR_PARAM,
    ERR_SWITCH,
    FailoverAIClient,
    build_failover_client,
    classify_ai_error,
    clear_ai_failover_state,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import AIModel, AIService

_REQ = httpx.Request("POST", "http://test")


def _http_err(cls, status, msg="err"):
    return cls(msg, response=httpx.Response(status, request=_REQ), body=None)


class _GenericStatusError(Exception):
    """A plain exception with status_code (goes through classify's generic status-code branch)."""

    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


class _FakeClient:
    """Scripted fake AIClient: each non-streaming call pops one script item (raises exceptions, returns anything else)."""

    def __init__(self, model="m", script=None):
        self.model = model
        self.base_url = "http://b"
        self.api_key = "k"
        self.total_tokens_used = 0
        self._script = list(script or [])
        self.temps: list = []
        self.calls = 0

    def _pop(self, temperature):
        self.temps.append(temperature)
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_multi(self, messages, temperature=0.4):
        return self._pop(temperature)

    async def chat(self, system_prompt, user_content, images=None, temperature=0.4):
        return self._pop(temperature)

    async def chat_with_tools(self, messages, tools, temperature=0.4):
        return self._pop(temperature)


# ── Error classification ───────────────────────────────────────────────────


def test_classify_timeout_switch():
    """A timeout means "switch models"."""
    assert classify_ai_error(APITimeoutError(request=_REQ)) == ERR_SWITCH


def test_classify_connection_switch():
    """A connection error means "switch models"."""
    assert classify_ai_error(APIConnectionError(message="c", request=_REQ)) == ERR_SWITCH


def test_classify_rate_limit_switch():
    """Rate limiting (429) means "switch models"."""
    assert classify_ai_error(_http_err(RateLimitError, 429)) == ERR_SWITCH


def test_classify_server_error_switch():
    """A server 5xx means "switch models"."""
    assert classify_ai_error(_http_err(InternalServerError, 500)) == ERR_SWITCH


def test_classify_auth_switch():
    """Expired auth (401) means "switch models" (a backup provider's key may work)."""
    assert classify_ai_error(_http_err(AuthenticationError, 401)) == ERR_SWITCH


def test_classify_param_incompatible_retry_same():
    """An incompatible parameter (400 + temperature hint) means "retry the same model without it"."""
    err = _http_err(BadRequestError, 400, "temperature is not supported for this model")
    assert classify_ai_error(err) == ERR_PARAM


def test_classify_business_400_fatal():
    """A clear 4xx business error (not a parameter one) means "raise"."""
    err = _http_err(BadRequestError, 400, "content policy violation")
    assert classify_ai_error(err) == ERR_FATAL


def test_classify_generic_4xx_fatal():
    """A generic exception with a 4xx status code means "raise"."""
    assert classify_ai_error(_GenericStatusError("not found", 404)) == ERR_FATAL


def test_classify_generic_5xx_switch():
    """A generic exception with a 5xx status code means "switch models"."""
    assert classify_ai_error(_GenericStatusError("bad gateway", 503)) == ERR_SWITCH


def test_classify_unknown_defaults_switch():
    """An unknown exception conservatively means "switch models" (the next candidate may be another provider)."""
    assert classify_ai_error(RuntimeError("mystery")) == ERR_SWITCH


# ── FailoverAIClient behaviour ──────────────────────────────────────────────


def test_failover_param_strip_retry_same_model():
    """Incompatible parameter: drop temperature and retry the same model, without switching."""
    clear_ai_failover_state()
    err = _http_err(BadRequestError, 400, "temperature unsupported")
    c = _FakeClient("m1", [err, "OK"])
    fc = FailoverAIClient([(c, "svc/m1")])

    result = asyncio.run(fc.chat_multi([{"role": "user", "content": "hi"}]))

    assert result == "OK"
    assert c.calls == 2
    assert c.temps == [0.4, None]  # temperature dropped the second time
    assert fc.used_model_label == "svc/m1"


def test_failover_switch_to_next_and_cooldown():
    """Main model 5xx: fall back to the next candidate; the failed one cools down and later calls skip it."""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500)])
    c2 = _FakeClient("m2", ["OK2"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "OK2"
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")  # the main model is cooling down

    # Second call: m1 is still in its cooldown window, so m2 is used directly without touching m1
    c2._script = ["OK3"]
    r2 = asyncio.run(fc.chat_multi([{"role": "user", "content": "y"}]))
    assert r2 == "OK3"
    assert c1.calls == 1  # m1 wasn't called again


def test_failover_fatal_raises_without_switch():
    """Fatal error (content/prompt): raise at once without trying the next candidate."""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(BadRequestError, 400, "content filter triggered")])
    c2 = _FakeClient("m2", ["unreachable"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(BadRequestError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert c2.calls == 0  # no fallback to the next candidate


def test_failover_all_cooling_recovery_probe():
    """Every candidate cooling down: probe the main candidate for recovery instead of failing."""
    clear_ai_failover_state()
    m._mark_fail("svc/m1")
    m._mark_fail("svc/m2")
    c1 = _FakeClient("m1", ["PRIMARY"])
    c2 = _FakeClient("m2", [])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "PRIMARY"
    assert c1.calls == 1
    assert not m._is_cooling("svc/m1")  # a successful probe clears the cooldown


def test_failover_chain_exhausted_raises_last_error():
    """Every candidate fails (recoverable kind): the last exception is raised."""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500, "boom1")])
    c2 = _FakeClient("m2", [_http_err(RateLimitError, 429, "boom2")])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(RateLimitError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))


# ── Streaming failover ──────────────────────────────────────────────────────


class _StreamRaiseBefore:
    """A fake client that fails before the stream starts (the first anext raises)."""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        raise _http_err(InternalServerError, 500, "stream boom")
        yield  # makes the function an async generator (unreachable)


class _StreamOK:
    """A fake client that yields some tokens normally."""

    model = "m2"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "hi")
        yield ("message", {"content": "hi", "tool_calls": []})


class _StreamRaiseAfter:
    """A fake client that fails after yielding one token."""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "par")
        raise _http_err(InternalServerError, 500, "mid-stream boom")


def test_failover_stream_switch_before_first_token():
    """Streaming: a failure before the first token can safely switch to the next candidate."""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseBefore(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        return [ev async for ev in fc.chat_stream([{"role": "user", "content": "x"}])]

    events = asyncio.run(run())
    assert ("token", "hi") in events
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")


def test_failover_stream_raise_after_started():
    """Streaming: a failure after tokens were yielded can't be rolled back; the exception propagates."""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseAfter(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        out = []
        async for ev in fc.chat_stream([{"role": "user", "content": "x"}]):
            out.append(ev)
        return out

    with pytest.raises(InternalServerError):
        asyncio.run(run())


# ── Candidate chain building ────────────────────────────────────────────────


def _mem_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_build_failover_client_chain_from_db():
    """Build the candidate chain: the main model first, the other DB models as backups by priority."""
    clear_ai_failover_state()
    db = _mem_session()
    svc = AIService(name="S", base_url="http://b", api_key="k")
    db.add(svc)
    db.commit()
    m1 = AIModel(name="M1", service_id=svc.id, model="glm-4", is_default=True)
    m2 = AIModel(name="M2", service_id=svc.id, model="glm-4-flash", is_default=False)
    db.add_all([m1, m2])
    db.commit()

    fc = build_failover_client(m1, svc, db=db)
    labels = [lbl for _, lbl in fc.candidates]
    assert labels[0] == "S/glm-4"  # main model first
    assert "S/glm-4-flash" in labels  # backups added
    db.close()


def test_build_failover_client_env_fallback(monkeypatch):
    """Without DB models, fall back to a single candidate from environment variables."""
    monkeypatch.setenv("AI_API_KEY", "test-key")
    clear_ai_failover_state()
    db = _mem_session()
    fc = build_failover_client(None, None, db=db)
    assert len(fc.candidates) == 1
    assert fc.candidates[0][1].startswith("env/")
    db.close()


# ── Runs record the model actually used ─────────────────────────────────────


def test_agent_context_model_label_reflects_used_model():
    """AgentContext.model_label reflects the model failover actually used (saved to agent_runs)."""
    from src.modules.automation.base import AgentContext

    fc = FailoverAIClient([(_FakeClient("m1"), "svc/m1"), (_FakeClient("m2"), "svc/m2")])
    ctx = AgentContext(ai_client=fc, notifier=None, config=None, model_label="svc/m1")

    # No switch: returns the main model label
    assert ctx.model_label == "svc/m1"

    # After failover: reflects the model that actually worked
    fc.used_model_label = "svc/m2"
    assert ctx.model_label == "svc/m2"
