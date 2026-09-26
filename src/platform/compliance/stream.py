"""Guard streamed model output sentence by sentence.

Tokens are held until a sentence boundary, then the complete sentence is checked and
either released unchanged or replaced by the redaction marker. Nothing is released
before it has been checked, so the stream can never show advice that the final message
would later remove. The full final message is guarded separately by the caller.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from src.platform.compliance.detector import detect
from src.platform.compliance.guard import REDACTION_MARKER, guard_text

# A sentence ends at CJK punctuation, a newline, or ASCII punctuation followed by space.
_BOUNDARY = re.compile(r"[.!?;](?=\s)|[\u3002\uff01\uff1f\uff1b\n]")


class GuardedTokenStream:
    def __init__(self, *, surface: str) -> None:
        self.surface = surface
        self._buffer = ""
        self.redactions = 0

    def _check(self, chunk: str) -> str:
        if not chunk.strip():
            return chunk
        if detect(chunk):
            self.redactions += 1
            trailing = chunk[len(chunk.rstrip()) :]
            return REDACTION_MARKER + (trailing or " ")
        return chunk

    def feed(self, token: str) -> list[str]:
        """Add a token; return the chunks that are safe to emit now."""
        self._buffer += token
        last_end = -1
        for match in _BOUNDARY.finditer(self._buffer):
            last_end = match.end()
        if last_end < 0:
            return []
        ready, self._buffer = self._buffer[:last_end], self._buffer[last_end:]
        # Keep whitespace that follows the boundary with the released chunk.
        stripped = self._buffer.lstrip(" \t")
        ready += self._buffer[: len(self._buffer) - len(stripped)]
        self._buffer = stripped
        return [self._check(ready)] if ready else []

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        ready, self._buffer = self._buffer, ""
        return [self._check(ready)]


async def guard_chat_stream(
    events: AsyncIterator[tuple[str, Any]], *, surface: str
) -> AsyncIterator[tuple[str, Any]]:
    """Wrap a ``chat_stream`` event iterator of ``("token", str)`` / ``("message", dict)``."""
    stream = GuardedTokenStream(surface=surface)
    async for kind, payload in events:
        if kind == "token":
            for chunk in stream.feed(str(payload or "")):
                yield "token", chunk
            continue
        if kind == "message" and isinstance(payload, dict):
            for chunk in stream.flush():
                yield "token", chunk
            content = payload.get("content")
            if content:
                payload = {**payload, "content": guard_text(str(content), surface=surface).text}
        yield kind, payload
    for chunk in stream.flush():
        yield "token", chunk
