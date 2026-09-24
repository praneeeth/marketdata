"""End-to-end sink tests with a stub model that always gives advice (PLAN.md Phase 1g).

Every user-facing path from ARCHITECTURE.md §5.4 receives only guarded text, and every
notification ends with the short disclaimer.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.compliance import BLOCKED_MESSAGE, SHORT_DISCLAIMER, is_guarded
from src.platform.compliance.detector import detect

ADVICE = (
    "Reliance looks strong. You should buy Reliance now with a target price of ₹3,200 "
    "and a stop loss at ₹2,800. Allocate 10% of your portfolio."
)


class AdviceClient:
    """Stands in for an AIClient whose model ignores every instruction."""

    total_tokens_used = 0
    last_usage = None

    async def chat(self, *_args: Any, **_kwargs: Any) -> str:
        return ADVICE

    async def chat_multi(self, *_args: Any, **_kwargs: Any) -> str:
        return ADVICE

    async def chat_with_tools(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(content=ADVICE, tool_calls=None)

    async def chat_stream(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        for token in ["Reliance looks strong. ", "You should ", "buy Reliance ", "now."]:
            yield "token", token
        yield "message", {"content": ADVICE, "tool_calls": [], "usage": None}


def _failover() -> Any:
    from src.platform.ai.ai_failover import FailoverAIClient

    return FailoverAIClient([(AdviceClient(), "stub/advice")])


def _memory_sessions() -> sessionmaker[Any]:
    from src.platform.persistence.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _assert_clean(text: str) -> None:
    assert detect(text) == [], text
    assert "You should buy" not in text


# ----------------------------------------------------------------- AI client


def test_failover_client_guards_every_method() -> None:
    client = _failover()

    async def run() -> tuple[str, str, Any, list[tuple[str, Any]]]:
        chat = await client.chat("system", "user")
        multi = await client.chat_multi([{"role": "user", "content": "x"}])
        tools = await client.chat_with_tools([{"role": "user", "content": "x"}], tools=[])
        events = [event async for event in client.chat_stream([{"role": "user", "content": "x"}])]
        return chat, multi, tools, events

    chat, multi, tools, events = asyncio.run(run())
    for text in (chat, multi, tools.content):
        assert is_guarded(text)
        _assert_clean(text)
    streamed = "".join(p for k, p in events if k == "token")
    _assert_clean(streamed)
    final = next(p for k, p in events if k == "message")
    _assert_clean(final["content"])


def test_assistant_model_adapter_emits_only_guarded_tokens() -> None:
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    emitted: list[str] = []

    async def emit(token: str) -> None:
        emitted.append(token)

    async def run() -> Any:
        from pan_agent import ModelMessage

        adapter = FailoverModelAdapter(_failover())
        return await adapter.run_turn([ModelMessage(role="user", content="x")], [], emit)

    turn = asyncio.run(run())
    _assert_clean("".join(emitted))
    _assert_clean(turn.content)


# ------------------------------------------------------------- notifications


def test_notifications_are_guarded_and_carry_disclaimer() -> None:
    from src.platform.notifications.notifier import NotifierManager

    manager = NotifierManager()
    manager.add_channel("telegram", {"bot_token": "123:abc", "chat_id": "1"})
    asyncio.run(manager.notify_with_result("Buy Reliance now", ADVICE))

    delivered = NotifierManager._deliver.call_args  # type: ignore[attr-defined]
    title, content = delivered.args[0], delivered.args[1]
    assert "Buy" not in title
    assert content.endswith(SHORT_DISCLAIMER)
    _assert_clean(content)
    assert len(content) <= 3500


def test_long_notification_keeps_disclaimer() -> None:
    from src.platform.notifications.notifier import NotifierManager

    manager = NotifierManager()
    manager.add_channel("telegram", {"bot_token": "123:abc", "chat_id": "1"})
    body = "\n".join(f"Line {i}: revenue grew {i}% year on year." for i in range(600))
    asyncio.run(manager.notify_with_result("Weekly wrap", body))
    content = NotifierManager._deliver.call_args.args[1]  # type: ignore[attr-defined]
    assert content.endswith(SHORT_DISCLAIMER)
    assert len(content) <= 3500


def test_agent_run_end_to_end() -> None:
    from src.modules.automation.base import AgentContext, AnalysisResult, BaseAgent
    from src.platform.notifications.notifier import NotifierManager
    from src.platform.runtime.config import AppConfig, Settings

    class StubAgent(BaseAgent):
        name = f"stub_{uuid.uuid4().hex[:8]}"
        display_name = "Stub research"

        async def collect(self, context: AgentContext) -> dict[str, Any]:
            return {}

        def build_prompt(self, data: dict[str, Any], context: AgentContext) -> tuple[str, str]:
            return "system", "user"

    notifier = NotifierManager()
    notifier.add_channel("telegram", {"bot_token": "123:abc", "chat_id": "1"})
    context = AgentContext(
        ai_client=_failover(),  # type: ignore[arg-type]
        notifier=notifier,
        config=AppConfig(settings=Settings(), watchlist=[]),
    )
    result: AnalysisResult = asyncio.run(StubAgent().run(context))
    _assert_clean(result.content)
    assert result.content.startswith(BLOCKED_MESSAGE)
    content = NotifierManager._deliver.call_args.args[1]  # type: ignore[attr-defined]
    assert content.endswith(SHORT_DISCLAIMER)
    _assert_clean(content)


def test_analysis_result_guards_reassignment() -> None:
    from src.modules.automation.base import AnalysisResult

    result = AnalysisResult(agent_name="x", title="Buy now", content="Revenue grew 9%.")
    assert "Buy" not in result.title
    result.content = ADVICE
    result.notify_content = ADVICE
    _assert_clean(result.content)
    _assert_clean(result.notify_content)


# ------------------------------------------------------------------- storage


def test_saved_history_is_guarded_and_sanitised(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.modules.research import analysis_history
    from src.platform.persistence.models import AnalysisHistory

    sessions = _memory_sessions()
    monkeypatch.setattr(analysis_history, "SessionLocal", sessions)
    assert analysis_history.save_analysis(
        agent_name="daily_report",
        stock_symbol="*",
        content=ADVICE,
        title="Buy these stocks",
        raw_data={
            "suggestions": {"RELIANCE": {"action": "buy", "action_label": "Buy"}},
            "news": [{"title": "Buy Reliance, target ₹3,000: brokerage", "url": "https://x/y"}],
            "research": [{"symbol": "RELIANCE", "summary": "Revenue grew 10%."}],
        },
    )
    row = sessions().query(AnalysisHistory).one()
    _assert_clean(row.content)
    assert "Buy" not in row.title
    assert "suggestions" not in row.raw_data
    assert row.raw_data["news"][0]["title"] == BLOCKED_MESSAGE
    assert row.raw_data["news"][0]["url"] == "https://x/y"
    assert row.raw_data["research"][0]["summary"] == "Revenue grew 10%."


def test_assistant_messages_are_guarded_when_persisted() -> None:
    from src.modules.assistant.repository import AssistantRepository

    session = _memory_sessions()()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(
        stock_symbol=None, stock_market=None, initial_context=None
    )
    message = repository.add_message(conversation, role="assistant", content=ADVICE)
    _assert_clean(message.content)
    user = repository.add_message(conversation, role="user", content="Should I buy?")
    assert user.content == "Should I buy?"


# ------------------------------------------------------------ TradingAgents


def test_tradingagents_research_mapping_has_no_decision() -> None:
    from src.modules.automation.tradingagents.decision import map_state_to_research_result

    state = {
        "final_trade_decision": (
            "### Summary\nDemand is recovering.\n**Rating**: Buy. Target ₹3,000."
        ),
        "market_report": "Price is above the 200-day average.",
        "sentiment_report": "Sentiment improved.",
        "news_report": "Buy Reliance, says brokerage.",
        "fundamentals_report": "Revenue grew 12%.",
        "investment_debate_state": {
            "history": "Bull: demand. Bear: debt.",
            "bull_history": "Bull: demand.",
            "bear_history": "Bear: debt.",
        },
        "trader_investment_plan": "Buy 100 shares.",
        "risk_debate_state": {"judge_decision": "Approve the buy."},
    }
    stock = SimpleNamespace(symbol="RELIANCE", name="Reliance Industries")
    result = map_state_to_research_result(
        stock=stock, ta_result={"final_state": state, "cost_usd": 0.02}, model_label="m"
    )
    _assert_clean(result.content)
    _assert_clean(result.notify_content or "")
    assert "Buy 100 shares" not in result.content
    assert set(result.raw_data) == {
        "mode",
        "research_summary",
        "cost_usd",
        "analyst_reports",
        "debate_history",
    }
    assert result.raw_data["analyst_reports"]["news"] == BLOCKED_MESSAGE
    assert result.raw_data["analyst_reports"]["fundamentals"] == "Revenue grew 12%."
    assert "Deep research" in result.title


def test_research_only_graph_has_no_decision_nodes() -> None:
    from tradingagents.graph.conditional_logic import ConditionalLogic

    from src.modules.automation.tradingagents.research_graph import (
        FORBIDDEN_NODES,
        SUMMARY_NODE,
        build_summary_prompt,
        create_research_summarizer,
        install_research_only_workflow,
        workflow_node_names,
    )

    class FakeLLM:
        def invoke(self, prompt: str) -> SimpleNamespace:
            assert "Never give ratings" in prompt
            return SimpleNamespace(content="### Summary\nRevenue grew 12%.")

        def bind_tools(self, _tools: Any) -> FakeLLM:
            return self

    setup = SimpleNamespace(
        quick_thinking_llm=FakeLLM(),
        deep_thinking_llm=FakeLLM(),
        tool_nodes={
            key: (lambda _state: {}) for key in ("market", "social", "news", "fundamentals")
        },
        conditional_logic=ConditionalLogic(),
    )
    graph = SimpleNamespace(graph_setup=setup, workflow=None, graph=None)
    install_research_only_workflow(graph, ["market", "social", "news", "fundamentals"])
    names = workflow_node_names(graph.workflow)
    assert SUMMARY_NODE in names
    assert not names & FORBIDDEN_NODES
    assert graph.graph is not None

    state = {
        "company_of_interest": "INFY",
        "trade_date": "2026-09-24",
        "market_report": "Trend up.",
        "investment_debate_state": {"history": "Bull and bear views.", "count": 2},
    }
    assert "Trend up." in build_summary_prompt(state)
    output = create_research_summarizer(FakeLLM())(state)
    assert output["investment_plan"] == ""
    assert output["trader_investment_plan"] == ""
    assert output["final_trade_decision"].startswith("### Summary")
    assert output["investment_debate_state"]["judge_decision"] == ""
    assert "(no analyst reports)" in build_summary_prompt({})


# ---------------------------------------------------------------------- PDF


def test_research_pdf_layout_has_no_decision() -> None:
    from src.modules.reporting.pdf_export import assemble_report_markdown

    markdown = assemble_report_markdown(
        {
            "research_summary": "Demand is recovering.",
            "analyst_reports": {"market": "Trend up.", "news": "Orders rose."},
            "debate_history": {"history": "Bull and bear views."},
        }
    )
    assert "Research summary" in markdown
    assert "持有" not in markdown
    assert "最终决策" not in markdown
    assert "Bull and bear debate" in markdown
