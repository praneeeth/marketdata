"""Regression tests for the TradingAgents run lifecycle and the collection stage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _query_chain(value):
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = value
    return query


def _db(active_run=None, latest_log=None):
    db = MagicMock()
    db.query.side_effect = [_query_chain(active_run), _query_chain(latest_log)]
    return db


def _run(status="running", created_at=None):
    run = MagicMock()
    run.status = status
    run.trace_id = "man-tradingagents-AAPL-123"
    run.created_at = created_at or datetime.now(timezone.utc)
    run.agent_name = "tradingagents"
    run.result = ""
    run.error = ""
    run.duration_ms = 0
    run.model_label = ""
    run.notify_sent = False
    return run


def test_running_agent_run_is_active_without_progress_log():
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    trace_id = find_active_tradingagents_trace(
        _db(_run(created_at=datetime.now(timezone.utc) - timedelta(seconds=30))),
        "AAPL",
    )

    assert trace_id == "man-tradingagents-AAPL-123"


def test_progress_uses_running_agent_run_when_logs_are_empty():
    from src.modules.automation.api.agents import get_run_progress

    run = _run(created_at=datetime.now(timezone.utc) - timedelta(seconds=42))
    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [log_query, run_query]

    with patch(
        "src.modules.automation.tradingagents.observability.aggregate_progress",
        return_value={"stages": []},
    ):
        result = get_run_progress("man-tradingagents-AAPL-123", db)

    assert result["status"] == "running"
    assert result["elapsed_sec"] >= 40
    assert result["run"]["status"] == "running"


def test_running_agent_run_is_stale_after_lifecycle_timeout():
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    old_run = _run(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert find_active_tradingagents_trace(_db(old_run), "AAPL") is None


def test_progress_marks_expired_running_agent_run_stale():
    from src.modules.automation.api.agents import get_run_progress

    run = _run(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db = MagicMock()
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [log_query, run_query]

    with patch(
        "src.modules.automation.tradingagents.observability.aggregate_progress",
        return_value={"stages": []},
    ):
        result = get_run_progress(run.trace_id, db)

    assert result["status"] == "stale"


def test_record_agent_run_updates_existing_running_row():
    from src.modules.automation import agent_runs

    existing = _run()
    db = MagicMock()
    running_query = MagicMock()
    running_query.filter.return_value.order_by.return_value.first.return_value = existing
    db.query.return_value = running_query

    with patch.object(agent_runs, "SessionLocal", return_value=db):
        agent_runs.record_agent_run(
            agent_name="tradingagents",
            status="success",
            result="done",
            trace_id=existing.trace_id,
            duration_ms=123,
        )

    assert existing.status == "success"
    assert existing.result == "done"
    assert existing.duration_ms == 123
    db.add.assert_not_called()


def test_start_agent_run_persists_running_state():
    from src.modules.automation import agent_runs

    db = MagicMock()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value = query

    with patch.object(agent_runs, "SessionLocal", return_value=db):
        agent_runs.start_agent_run(
            agent_name="tradingagents",
            trace_id="man-tradingagents-AAPL-123",
            trigger_source="manual",
        )

    db.add.assert_called_once()
    assert db.add.call_args.args[0].status == "running"


def test_data_collection_is_a_visible_progress_stage():
    from src.modules.automation.tradingagents.observability import STAGES_ORDER, aggregate_progress

    assert STAGES_ORDER[0] == "data_collection"
    result = aggregate_progress([
        {
            "timestamp": "2026-09-19T10:00:00+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "stage_start",
                "elapsed_sec": 0.1,
            },
        },
    ])
    assert result["current_stage"] == "data_collection"
    assert result["stages"][0]["status"] == "running"


def test_data_collection_source_error_is_visible_in_progress_snapshot():
    from src.modules.automation.tradingagents.observability import aggregate_progress

    result = aggregate_progress([
        {
            "timestamp": "2026-09-19T10:00:00+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "source_start",
                "source": "quote",
            },
        },
        {
            "timestamp": "2026-09-19T10:00:01+00:00",
            "tags": {
                "stage": "data_collection",
                "action": "source_error",
                "source": "quote",
                "error": "Yahoo 429",
            },
        },
    ])

    assert result["data_sources"] == [
        {"name": "quote", "status": "error", "error": "Yahoo 429"},
    ]


def test_progress_exposes_active_llm_tool_operation():
    """While an LLM/tool call hasn't finished, the snapshot tells the frontend which operation it's stuck on."""
    from src.modules.automation.tradingagents.observability import aggregate_progress

    result = aggregate_progress([
        {
            "timestamp": "2026-09-19T10:00:00+00:00",
            "tags": {"stage": "market_analyst", "action": "stage_start"},
        },
        {
            "timestamp": "2026-09-19T10:00:01+00:00",
            "tags": {
                "stage": "llm_call",
                "action": "tool_start",
                "tool": "get_verified_market_snapshot",
            },
        },
    ])

    assert result["active_operation"] == {
        "kind": "tool",
        "name": "get_verified_market_snapshot",
    }


def test_progress_active_operation_includes_agent_when_callback_provides_it():
    from src.modules.automation.tradingagents.observability import aggregate_progress

    result = aggregate_progress([{
        "timestamp": "2026-09-19T10:00:01+00:00",
        "tags": {
            "stage": "llm_call",
            "action": "tool_start",
            "tool": "get_stock_data",
            "agent": "Market Analyst",
        },
    }])

    assert result["active_operation"] == {
        "kind": "tool",
        "name": "get_stock_data",
        "agent": "Market Analyst",
    }


def test_progress_keeps_other_parallel_tool_active_after_one_finishes():
    """When one of several parallel tools finishes, the other long request still shows as the current operation."""
    from src.modules.automation.tradingagents.observability import aggregate_progress

    result = aggregate_progress([
        {"timestamp": "2026-09-19T10:00:00+00:00", "tags": {
            "stage": "llm_call", "action": "tool_start", "tool": "get_stock_data", "operation_id": "a",
        }},
        {"timestamp": "2026-09-19T10:00:00+00:00", "tags": {
            "stage": "llm_call", "action": "tool_start", "tool": "get_verified_market_snapshot", "operation_id": "b",
        }},
        {"timestamp": "2026-09-19T10:00:01+00:00", "tags": {
            "stage": "llm_call", "action": "tool_end", "tool": "get_stock_data", "operation_id": "a",
        }},
    ])

    assert result["active_operation"] == {
        "kind": "tool",
        "name": "get_verified_market_snapshot",
    }


def test_progress_handler_uses_run_id_to_close_the_same_langgraph_node():
    """LangChain 1.x's on_chain_end no longer reliably provides name, so it must be linked by run_id."""
    from src.modules.automation.tradingagents.observability import PanWatchProgressHandler

    handler = PanWatchProgressHandler(trace_id="trace-1")
    emitted = []
    handler._emit = lambda stage, action, **extra: emitted.append((stage, action, extra))

    handler.on_chain_start(
        {"name": "Market Analyst"},
        {},
        name="Market Analyst",
        run_id="node-1",
        metadata={"langgraph_node": "Market Analyst"},
    )
    # Real LangChain 1.x callbacks only have run_id/parent_run_id here, no name.
    handler.on_chain_end({}, run_id="node-1", parent_run_id="root-1")

    assert [(stage, action) for stage, action, _ in emitted] == [
        ("market_analyst", "stage_start"),
        ("market_analyst", "stage_end"),
    ]
    assert emitted[-1][2]["langgraph_node"] == "Market Analyst"
    assert emitted[-1][2]["run_id"] == "node-1"


def test_progress_handler_drops_empty_node_name_instead_of_data_collection():
    """An empty name must not match `n in stage`, or every unknown end event would become 'data collection done'."""
    from src.modules.automation.tradingagents.observability import PanWatchProgressHandler

    handler = PanWatchProgressHandler(trace_id="trace-2")
    emitted = []
    handler._emit = lambda stage, action, **extra: emitted.append((stage, action, extra))

    handler.on_chain_end({}, run_id="unknown-1")

    assert emitted == []


def test_progress_handler_exposes_agent_for_llm_and_tool_operations():
    """The active operation must say which sub-agent started it, so the UI shows more than a generic tool name."""
    from src.modules.automation.tradingagents.observability import PanWatchProgressHandler

    handler = PanWatchProgressHandler(trace_id="trace-3")
    emitted = []
    handler._emit = lambda stage, action, **extra: emitted.append((stage, action, extra))

    handler.on_llm_start(
        {"name": "ChatOpenAI"},
        ["prompt"],
        run_id="llm-1",
        parent_run_id="node-1",
        metadata={"langgraph_node": "Market Analyst"},
    )
    handler.on_tool_start(
        {"name": "get_stock_data"},
        "601238",
        run_id="tool-1",
        parent_run_id="node-1",
        metadata={"langgraph_node": "Market Analyst"},
    )

    assert emitted[0][2]["agent"] == "Market Analyst"
    assert emitted[1][2]["agent"] == "Market Analyst"


def test_progress_handler_maps_upstream_researcher_aliases():
    from src.modules.automation.tradingagents.observability import _normalize_stage

    assert _normalize_stage("Sentiment Analyst") == "social_analyst"
    assert _normalize_stage("Bull Researcher") == "bull_bear_debate"
    assert _normalize_stage("Conservative Analyst") == "risk_judge"
    assert _normalize_stage("Portfolio Manager") == "final_decision"


def test_one_market_source_failure_does_not_zero_other_sources():
    """A failing quote source must not blank the candles or technical indicators."""
    import asyncio

    from src.modules.automation.tradingagents.agent import TradingAgentsAgent

    async def _run():
        agent = TradingAgentsAgent(collection_timeout_seconds=5)
        stock = MagicMock(symbol="INFY", name="Infosys")
        stock.market.value = "IN"
        context = MagicMock()
        context.watchlist = [stock]
        context._trace_id = "man-tradingagents-INFY-123"

        collector = MagicMock()
        collector.return_value.get_klines.return_value = ["bar"]
        collector.return_value.get_technical_indicators.return_value = {"rsi": 52}
        with (
            patch(
                "src.platform.marketdata.marketdata_client.md_quote_rows",
                side_effect=RuntimeError("broker 429"),
            ),
            patch("src.platform.marketdata.collectors.kline_collector.KlineCollector", collector),
        ):
            result = await agent.collect(context)

        assert result["quote"] == {}
        assert result["klines"] == ["bar"]
        assert result["capital_flow"] == []  # China-only source removed
        assert result["events"] == []
        assert result["technical"] == {"rsi": 52}

    asyncio.run(_run())


def test_empty_required_market_source_is_visible_as_error(monkeypatch):
    """An empty required source (candles) is reported as source_error, not source_end."""
    import asyncio

    from src.modules.automation.tradingagents import agent as agent_module
    from src.modules.automation.tradingagents.agent import TradingAgentsAgent

    class _Handler:
        events = []

        def __init__(self, *args, **kwargs):
            self.trace_id = args[0] if args else ""
            self.events = []

        def emit(self, stage, action, **extra):
            self.events.append((stage, action, extra))

    async def _run():
        agent = TradingAgentsAgent(collection_timeout_seconds=5)
        stock = MagicMock(symbol="INFY", name="Infosys")
        stock.market.value = "IN"
        context = MagicMock()
        context.watchlist = [stock]
        context._trace_id = "man-tradingagents-INFY-empty"

        monkeypatch.setattr(agent_module, "PanWatchProgressHandler", _Handler)
        monkeypatch.setattr("src.platform.marketdata.marketdata_client.md_quote_rows", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.platform.marketdata.collectors.kline_collector.KlineCollector.get_klines",
            lambda self, symbol, days=60: [],
        )
        monkeypatch.setattr(
            "src.platform.marketdata.collectors.kline_collector.KlineCollector.get_technical_indicators",
            lambda self, symbol, klines=None: {},
        )

        await agent.collect(context)
        events = context._progress_handler.events
        assert any(
            action == "source_error" and extra.get("source") == "klines"
            for _, action, extra in events
        )

    asyncio.run(_run())
