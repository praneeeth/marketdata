"""Unit tests for the chat SSE endpoint: event sequence / tool loop / fallback / resume after disconnect (fully mocked, no real requests)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.assistant.chat_api as chat_api
from src.platform.events.sse import SSEStream
from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation, ChatMessage


def _make_session_factory():
    """In-memory SQLite session factory (StaticPool keeps one connection)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _setup_conversation(session_factory, content="How are my holdings?"):
    """Create a conversation + user message and return conversation_id."""
    db = session_factory()
    conv = ChatConversation(title="test")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(ChatMessage(conversation_id=conv.id, role="user", content=content))
    db.commit()
    conv_id = conv.id
    db.close()
    return conv_id


class _FakeAIClient:
    """A fake AI client that answers round by round from a script.

    Each item of rounds is:
    - ("tokens", ["Hel", "lo"]): stream the text this round and finish (no tool calls);
    - ("tools", [{"id","name","arguments"}, ...]): this round asks for tool calls;
    - "raise": this round's streaming call raises (triggers the fallback path).
    """

    def __init__(self, rounds, chat_multi_result="fallback answer", chat_multi_raises=False):
        self._rounds = list(rounds)
        self._chat_multi_result = chat_multi_result
        self._chat_multi_raises = chat_multi_raises
        self.model = "fake-model"

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        assert self._rounds, "script rounds used up"
        round_spec = self._rounds.pop(0)
        if round_spec == "raise":
            raise RuntimeError("stream unsupported")
        kind, payload = round_spec
        if kind == "tokens":
            for t in payload:
                yield ("token", t)
            yield ("message", {"content": "".join(payload), "tool_calls": []})
        else:
            yield ("message", {"content": "", "tool_calls": payload})

    async def chat_multi(self, messages, temperature=0.4):
        if self._chat_multi_raises:
            raise RuntimeError("multi also failed")
        return self._chat_multi_result


def _run_task_and_collect(monkeypatch, session_factory, ai_client, conv_id):
    """Run _run_chat_stream_task and collect every event; returns [(event, data_dict), ...]."""
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)
    monkeypatch.setattr(chat_api, "_get_ai_client", lambda db, model_id=None: ai_client)

    async def run():
        stream = SSEStream()
        await chat_api._run_chat_stream_task(conv_id, stream)
        events = []
        async for raw in stream.subscribe(after_seq=0):
            lines = raw.strip().split("\n")
            event = next(l.split(": ", 1)[1] for l in lines if l.startswith("event: "))
            data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
            events.append((event, json.loads(data_raw)))
        return events

    return asyncio.run(run())


def test_stream_task_plain_answer(monkeypatch):
    """No tool calls: token events go out one by one; done carries the full answer, already saved."""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient([("tokens", ["Hel", "lo"])])

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "token", "done"]
    assert events[0][1]["text"] == "Hel"
    assert events[-1][1]["content"] == "Hello"

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .all()
    )
    db.close()
    assert len(saved) == 1
    assert saved[0].content == "Hello"


def test_stream_task_tool_loop(monkeypatch):
    """Tool loop: tool_call_start/tool_result events go first; the final answer comes as a token stream."""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory, content="What is Infosys trading at?")
    ai = _FakeAIClient([
        ("tools", [{"id": "c1", "name": "get_stock_quote", "arguments": '{"symbol": "600519"}'}]),
        ("tokens", ["Infosys at 1700"]),
    ])
    fake_exec = AsyncMock(return_value="Live quote: Infosys price 1700")
    monkeypatch.setattr(chat_api, "_execute_tool", fake_exec)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["tool_call_start", "tool_result", "token", "done"]
    assert events[0][1] == {"name": "get_stock_quote", "arguments": {"symbol": "600519"}}
    assert events[1][1]["ok"] is True
    assert "1700" in events[1][1]["preview"]
    assert events[-1][1]["content"] == "Infosys at 1700"
    # The tool name and arguments really reached the executor
    fake_exec.assert_awaited_once()
    assert fake_exec.await_args.args[1] == "get_stock_quote"
    assert fake_exec.await_args.args[2] == {"symbol": "600519"}


def test_stream_task_fallback_to_chat_multi(monkeypatch):
    """Streaming unavailable: falls back to chat_multi; the whole text goes out as one token event and is saved."""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_result="fallback answer")

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "done"]
    assert events[0][1]["text"] == "fallback answer"
    assert events[-1][1]["content"] == "fallback answer"


def test_stream_task_error_event(monkeypatch):
    """AI completely unavailable: an error event is pushed and the error text is saved (same as non-streaming)."""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_raises=True)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["error", "done"]
    assert "multi also failed" in events[0][1]["message"]
    assert "AI service is unavailable" in events[-1][1]["content"]

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .first()
    )
    db.close()
    assert "AI service is unavailable" in saved.content


def test_send_message_stream_endpoint(monkeypatch):
    """Streaming endpoint: saves the user message; the first meta event carries stream_id, resumable through the hub."""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)

    async def fake_task(conversation_id, stream, task_id=None):
        await stream.publish("done", {"message_id": 1, "content": "x"})
        await stream.finish()

    monkeypatch.setattr(chat_api, "_run_chat_stream_task", fake_task)

    async def run():
        resp = await chat_api.send_message_stream(
            conv_id, chat_api.SendMessageBody(content="second question")
        )
        assert resp.media_type == "text/event-stream"
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "event: meta\n" in body
    assert "event: done\n" in body

    # The stream_id in meta can be found in the hub again (the basis for reconnecting)
    meta_line = next(
        l for l in body.split("\n") if l.startswith("data: ") and "stream_id" in l
    )
    stream_id = json.loads(meta_line[len("data: "):])["stream_id"]
    assert chat_api.chat_stream_hub.get(stream_id) is not None
    assert json.loads(meta_line[len("data: "):])["task_id"] > 0

    # The user message was saved
    db = session_factory()
    user_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
        .all()
    )
    db.close()
    assert any(m.content == "second question" for m in user_msgs)


def test_resume_stream_not_found():
    """Reconnect: an unknown/expired stream_id returns 404."""
    request = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_api.resume_message_stream("nonexistent", request, 0))
    assert ei.value.status_code == 404


def test_resume_stream_last_event_id(monkeypatch):
    """Reconnect: the Last-Event-ID header wins over the query parameter; resumes after it."""
    async def run():
        stream = chat_api.chat_stream_hub.create()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.finish()

        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await chat_api.resume_message_stream(stream.stream_id, request, 0)
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "id: 1\n" not in body
    assert "id: 2\n" in body
