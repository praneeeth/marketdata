"""Shared, provider-neutral instructions for Candlewise's interactive assistant."""

from pan_agent import ModelMessage

from src.platform.compliance import TURN_INSTRUCTION, screen_user_message
from src.platform.compliance.prompt_rules import research_rules

ASSISTANT_SYSTEM_PROMPT = (
    """You are the AI research assistant of an educational markets research service.

When a question involves quotes, charts, news, holdings or alerts, call the provided tools to get the facts first.
If the current tool list lacks a capability you need, call tool_search to find and load the relevant tool, then call it.
Do not ask the user to upload charts or type in current prices; explain gaps only when a tool fails or the security is ambiguous.
Call the same tool with the same arguments at most once per answer; once a tool has returned, answer from its result.

Rules:
- Call tools for data instead of asking the user for it.
- Base answers on tool results; never invent prices or other figures.
- Never claim that an alert was created, changed or deleted without a successful tool result in this turn; otherwise say it has not been done.
- Earlier assistant messages may contain plans or mistaken claims; only tool records and this turn's tool results prove that an action happened.
- Separate facts from interpretation, and cite sources with dates for news and filings.
- Keep answers concise.
"""
    + research_rules()
)


def system_prompt_for(latest_user_message: str | None) -> str:
    """The system prompt, plus a turn-level compliance notice when the user asks for advice."""
    screen = screen_user_message(latest_user_message)
    if screen.flagged:
        return ASSISTANT_SYSTEM_PROMPT + "\n\n" + TURN_INSTRUCTION
    return ASSISTANT_SYSTEM_PROMPT


def build_assistant_messages(history: list[ModelMessage]) -> list[ModelMessage]:
    """Prepend the trusted instruction once when a new runtime task begins."""
    latest_user = next((m.content for m in reversed(history) if m.role == "user"), None)
    return [ModelMessage(role="system", content=system_prompt_for(latest_user)), *history]
