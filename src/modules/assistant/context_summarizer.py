"""Model-backed and deterministic context summary adapters for the assistant host."""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from typing import Any

from pan_agent import ContextCompressionMode, ContextSummary, ModelMessage

_SUMMARY_SYSTEM_PROMPT = """You compress conversation context.
Output exactly one valid JSON object: no Markdown, explanation or extra text.
The JSON must have the fields goal, constraints, decisions, facts, current_state, open_items, tool_findings.
The first six list fields are string arrays and current_state is a string; keep only facts that help later answers.
Don't invent prices, dates, people or decisions that didn't appear in the conversation.
"""


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return text


class FailoverContextSummarizer:
    """Use the host's configured failover client for structured summaries."""

    def __init__(
        self,
        client: Any,
        *,
        temperature: float = 0.1,
        max_summary_tokens: int = 800,
    ) -> None:
        self._client = client
        self._temperature = temperature
        self._max_summary_tokens = max_summary_tokens

    async def summarize(
        self,
        messages: Sequence[ModelMessage],
        *,
        mode: ContextCompressionMode,
    ) -> ContextSummary:
        transcript = "\n\n".join(
            f"[{message.role}] {message.content}" for message in messages if message.content
        )
        request_messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Compression mode: {mode.value}\n"
                    f"Use at most about {self._max_summary_tokens} tokens for the summary.\n"
                    "Turn the earlier conversation below into a structured summary that can be carried forward.\n\n"
                    + transcript
                ),
            },
        ]
        parameters = inspect.signature(self._client.chat_multi).parameters
        supports_max_tokens = "max_tokens" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        request_kwargs = {"temperature": self._temperature}
        if supports_max_tokens:
            request_kwargs["max_tokens"] = self._max_summary_tokens
        raw = await self._client.chat_multi(request_messages, **request_kwargs)
        payload = raw if isinstance(raw, dict) else json.loads(_strip_json_fence(str(raw)))
        return ContextSummary.model_validate(payload)
