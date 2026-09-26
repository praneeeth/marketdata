"""Unit tests for the SSE infrastructure: event encoding / stream buffer resume / middleware pass-through."""

import asyncio
import json

from src.platform.events.sse import SSEHub, SSEStream, format_sse_event
from src.web.response import ResponseWrapperMiddleware


def test_format_sse_event():
    """SSE event encoding: with id/event/data; a dict becomes JSON."""
    text = format_sse_event(3, "token", {"text": "hello"})
    assert "id: 3\n" in text
    assert "event: token\n" in text
    assert 'data: {"text": "hello"}\n' in text
    assert text.endswith("\n\n")


def test_format_sse_event_multiline():
    """SSE event encoding: data with newlines is split into several data: lines."""
    text = format_sse_event(1, "token", "a\nb")
    assert "data: a\ndata: b\n" in text


def test_stream_replay_and_resume():
    """Event stream: subscribing after publishing replays every event; after_seq resumes only the later ones."""

    async def run():
        stream = SSEStream()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.publish("done", {})
        await stream.finish()

        # Subscribe from the start: all 3 received
        all_events = [ev async for ev in stream.subscribe(after_seq=0)]
        assert len(all_events) == 3
        assert "id: 1\n" in all_events[0]

        # Reconnect: Last-Event-ID=2 -> only the 3rd is received
        resumed = [ev async for ev in stream.subscribe(after_seq=2)]
        assert len(resumed) == 1
        assert "id: 3\n" in resumed[0]
        assert "event: done\n" in resumed[0]

    asyncio.run(run())


def test_stream_live_subscribe():
    """Event stream: the subscriber waits, receives at once when the producer publishes, and exits after finish."""

    async def run():
        stream = SSEStream()
        received: list[str] = []

        async def consumer():
            async for ev in stream.subscribe(after_seq=0):
                received.append(ev)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        await stream.publish("token", {"text": "hi"})
        await asyncio.sleep(0.01)
        await stream.finish()
        await asyncio.wait_for(task, timeout=2)
        assert len(received) == 1
        assert "event: token\n" in received[0]

    asyncio.run(run())


def test_hub_create_get_prune():
    """Hub: create/get work; streams past the TTL are cleaned up."""
    hub = SSEHub(ttl_sec=0.0)  # TTL=0 -> cleaned up on the next prune
    stream = hub.create()
    # With TTL 0, the prune triggered by get has already cleaned it up
    assert hub.get(stream.stream_id) is None

    hub2 = SSEHub(ttl_sec=60)
    s2 = hub2.create()
    assert hub2.get(s2.stream_id) is s2


def _make_scope(path="/api/chat/x"):
    return {"type": "http", "path": path}


def test_middleware_sse_passthrough():
    """Middleware: text/event-stream responses pass through chunk by chunk, unbuffered."""

    async def run():
        sent_during_app: list[int] = []

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            })
            await send({"type": "http.response.body", "body": b"id: 1\n\n", "more_body": True})
            # Record how many messages downstream has at this moment; in pass-through mode they are forwarded in real time
            sent_during_app.append(len(sent_messages))
            await send({"type": "http.response.body", "body": b"id: 2\n\n", "more_body": False})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        # Before the app sends the second chunk, start + the first chunk have reached downstream (so nothing is buffered)
        assert sent_during_app == [2]
        assert len(sent_messages) == 3
        assert sent_messages[1]["body"] == b"id: 1\n\n"

    asyncio.run(run())


def test_middleware_json_still_wrapped():
    """Middleware: ordinary JSON responses are still wrapped as {code, success, data, message}."""

    async def run():
        async def app(scope, receive, send):
            body = json.dumps({"hello": "world"}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        body = json.loads(sent_messages[-1]["body"])
        assert body["code"] == 0
        assert body["success"] is True
        assert body["data"] == {"hello": "world"}

    asyncio.run(run())
