"""SSE (Server-Sent Events) infrastructure.

Two pieces:
1. `format_sse_event`: encodes an event in the SSE wire format (with an increasing id for Last-Event-ID resume).
2. `SSEStream` / `SSEHub`: an event buffer that decouples generation from the connection.
   - Producers (background tasks) publish events to an `SSEStream` independently of the HTTP connection, so a disconnect doesn't stop generation;
   - consumers (SSE endpoints) subscribe from any sequence number, so a reconnect with Last-Event-ID resumes;
   - after the stream finishes it is kept for a while (TTL) so a late reconnect can read every event.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

# How long a finished stream is kept (seconds): long enough for the frontend to reconnect and get the full result
STREAM_TTL_SEC = 600
# Maximum events per stream (a defensive cap so a runaway task can't exhaust memory)
MAX_EVENTS_PER_STREAM = 10000


def format_sse_event(seq: int, event: str, data: dict | str) -> str:
    """Encode one SSE event (id + event + data; data is always JSON)."""
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    # data with newlines is split into several data: lines, per the SSE protocol
    data_lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"id: {seq}\nevent: {event}\n{data_lines}\n"


def format_sse_comment(text: str = "keepalive") -> str:
    """Encode an SSE comment line (heartbeat, so proxies don't drop idle connections)."""
    return f": {text}\n\n"


@dataclass
class _Event:
    seq: int
    event: str
    data: dict | str


@dataclass
class SSEStream:
    """A replayable event stream (producer and consumer decoupled)."""

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    done: bool = False

    def __post_init__(self):
        self._events: list[_Event] = []
        self._cond = asyncio.Condition()

    async def publish(self, event: str, data: dict | str) -> int:
        """Append an event and return its sequence number (starting at 1)."""
        async with self._cond:
            if len(self._events) >= MAX_EVENTS_PER_STREAM:
                # Over the cap: mark it finished at once to avoid unbounded growth
                self.done = True
                self._cond.notify_all()
                return len(self._events)
            seq = len(self._events) + 1
            self._events.append(_Event(seq=seq, event=event, data=data))
            self._cond.notify_all()
            return seq

    async def finish(self) -> None:
        """Mark the stream finished (subscribers exit once they have read the buffer)."""
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0, heartbeat_sec: float = 15.0):
        """Consume events after after_seq (an async generator yielding SSE wire-format strings).

        - First replays events already in the buffer (the key to Last-Event-ID resume);
        - once caught up, waits for new events, yielding a heartbeat comment after heartbeat_sec;
        - ends when the stream is done and the buffer is read.
        """
        cursor = max(0, int(after_seq))
        while True:
            batch: list[_Event] = []
            async with self._cond:
                if cursor < len(self._events):
                    batch = self._events[cursor:]
                    cursor = len(self._events)
                elif self.done:
                    return
                else:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=heartbeat_sec)
                    except asyncio.TimeoutError:
                        pass
            if batch:
                for ev in batch:
                    yield format_sse_event(ev.seq, ev.event, ev.data)
            else:
                async with self._cond:
                    idle = not (cursor < len(self._events) or self.done)
                if idle:
                    yield format_sse_comment()


class SSEHub:
    """Manages several SSEStreams by stream_id, with TTL cleanup."""

    def __init__(self, ttl_sec: float = STREAM_TTL_SEC):
        self._streams: dict[str, SSEStream] = {}
        self._ttl_sec = ttl_sec

    def create(self) -> SSEStream:
        self._prune()
        stream = SSEStream()
        self._streams[stream.stream_id] = stream
        return stream

    def get(self, stream_id: str) -> SSEStream | None:
        self._prune()
        return self._streams.get(stream_id)

    def _prune(self) -> None:
        """Remove old streams past the TTL."""
        now = time.monotonic()
        expired = [
            sid for sid, s in self._streams.items()
            if now - s.created_at > self._ttl_sec
        ]
        for sid in expired:
            self._streams.pop(sid, None)


# Global hub for chat streams (per-process singleton; decouples generation tasks from SSE connections)
chat_stream_hub = SSEHub()
