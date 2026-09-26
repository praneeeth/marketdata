"""Unit tests for the AIClient.chat_stream channel (fully mocked, no real requests)."""

import asyncio
from types import SimpleNamespace

from src.platform.ai.ai_client import AIClient


def _chunk(content=None, tool_calls=None, usage=None):
    """A minimal stand-in for an OpenAI streaming chunk."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tc_delta(index, id="", name="", arguments=""):
    """A tool_call increment piece."""
    fn = SimpleNamespace(name=name or None, arguments=arguments or None)
    return SimpleNamespace(index=index, id=id or None, function=fn)


class _FakeStream:
    """Simulates AsyncOpenAI's streaming response (an async iterator)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _make_client(chunks):
    """An AIClient whose completions.create returns a fixed chunk sequence."""
    client = AIClient(base_url="http://mock", api_key="mock", model="mock-model")

    async def fake_create(**kwargs):
        assert kwargs.get("stream") is True
        return _FakeStream(chunks)

    client.client.chat.completions.create = fake_create
    return client


def _collect(client, **kwargs):
    """Collect every event chat_stream yields."""

    async def run():
        events = []
        async for ev in client.chat_stream([{"role": "user", "content": "hi"}], **kwargs):
            events.append(ev)
        return events

    return asyncio.run(run())


def test_stream_tokens():
    """Plain text stream: yields token by token, then the complete message."""
    client = _make_client([
        _chunk(content="Hel"),
        _chunk(content="lo"),
        _chunk(content="!"),
    ])
    events = _collect(client)
    tokens = [t for kind, t in events if kind == "token"]
    assert tokens == ["Hel", "lo", "!"]
    kind, msg = events[-1]
    assert kind == "message"
    assert msg["content"] == "Hello!"
    assert msg["tool_calls"] == []


def test_stream_tool_calls_assembled():
    """Tool call stream: argument pieces are merged by index into complete tool_calls."""
    client = _make_client([
        _chunk(tool_calls=[_tc_delta(0, id="call_1", name="get_stock_quote")]),
        _chunk(tool_calls=[_tc_delta(0, arguments='{"symbol"')]),
        _chunk(tool_calls=[_tc_delta(0, arguments=': "600519"}')]),
        _chunk(tool_calls=[_tc_delta(1, id="call_2", name="get_portfolio", arguments="{}")]),
    ])
    events = _collect(client, tools=[{"type": "function", "function": {"name": "x"}}])
    kind, msg = events[-1]
    assert kind == "message"
    assert msg["content"] == ""
    assert msg["tool_calls"] == [
        {"id": "call_1", "name": "get_stock_quote", "arguments": '{"symbol": "600519"}'},
        {"id": "call_2", "name": "get_portfolio", "arguments": "{}"},
    ]


def test_stream_forwards_required_tool_choice_to_provider():
    captured: dict = {}
    client = AIClient(base_url="http://mock", api_key="mock", model="mock-model")

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeStream([_chunk(tool_calls=[_tc_delta(0, id="call_1", name="update_price_alert")])])

    client.client.chat.completions.create = fake_create

    _collect(
        client,
        tools=[{"type": "function", "function": {"name": "update_price_alert"}}],
        tool_choice="required",
    )

    assert captured["tool_choice"] == "required"


def test_stream_usage_and_empty_choices():
    """A trailing usage-only chunk with empty choices doesn't error, and token usage accumulates."""
    usage = SimpleNamespace(
        prompt_tokens=30,
        completion_tokens=12,
        total_tokens=42,
        prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
    )
    client = _make_client([
        _chunk(content="ok"),
        SimpleNamespace(choices=[], usage=usage),
    ])
    events = _collect(client)
    assert events[-1][1]["content"] == "ok"
    assert events[-1][1]["usage"] == {
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
        "cached_input_tokens": 10,
        "reasoning_output_tokens": 4,
        "model": "mock-model",
        "source": "provider",
    }
    assert client.total_tokens_used == 42
