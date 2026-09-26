"""PanWatch business tool adapters exposed to the generic agent runtime."""

import asyncio
from unittest.mock import MagicMock
from types import SimpleNamespace

from pan_agent import ModelMessage, ReadOnlyToolPolicy, RunRequest, ToolExposure
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.assistant.tools as assistant_tools
from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    Account,
    Position,
    PriceAlertHit,
    PriceAlertRule,
    Stock,
)


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _request() -> RunRequest:
    return RunRequest(
        run_id="tools-test", messages=[ModelMessage(role="user", content="test tools")]
    )


def test_panwatch_registry_keeps_core_tools_direct_and_defers_specialized_tools():
    engine, session = _session()
    registry = assistant_tools.build_panwatch_tool_registry(session)
    visible = {
        tool.name
        for tool in registry.model_tools(_request(), ReadOnlyToolPolicy())
    }

    assert {"get_stock_quote", "get_stock_news", "get_portfolio", "tool_search"} - visible == {
        "tool_search"
    }
    assert registry.get("create_price_alert").spec.exposure is ToolExposure.DEFERRED
    assert registry.model_tools(
        _request(), ReadOnlyToolPolicy(), names=["get_kline_summary"], include_deferred=True
    )[0].name == "get_kline_summary"
    for removed in ("get_hot_stocks", "get_hot_boards", "get_board_stocks",
                    "get_stock_fundamentals", "get_capital_flow", "get_dragon_tiger"):
        assert removed not in {spec.name for spec in registry.registered_tools()}
    session.close()
    engine.dispose()


def test_portfolio_tool_is_read_only_and_includes_provenance():
    engine, session = _session()
    stock = Stock(symbol="INFY", name="Infosys", market="IN")
    account = Account(name="Default account")
    session.add_all([stock, account])
    session.commit()
    session.add(
        Position(account_id=account.id, stock_id=stock.id, cost_price=1500, quantity=10)
    )
    session.commit()

    registry = assistant_tools.build_panwatch_tool_registry(session)
    result = asyncio.run(registry.execute("get_portfolio", _request(), {}))

    assert result.ok is True
    assert "Infosys" in result.summary
    assert result.sources[0].name == "PanWatch positions"
    session.close()
    engine.dispose()


def test_quote_tool_returns_compact_fact_summary(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_quote_rows",
        lambda *_: [
            {
                "symbol": "INFY",
                "name": "Infosys",
                "market": "IN",
                "current_price": 1800.0,
                "change_pct": 1.2,
                "high_price": 1812.0,
                "low_price": 1775.0,
                "open_price": 1780.0,
                "prev_close": 1778.0,
                "volume": 123.0,
                "turnover": 456.0,
            }
        ],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_quote",
            _request(),
            {"symbol": "INFY", "market": "IN"},
        )
    )

    assert result.ok is True
    assert result.data["symbol"] == "INFY"
    assert "1800" in result.summary
    session.close()
    engine.dispose()


def test_quote_tool_returns_controlled_failure_without_quote(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "md_quote_rows", lambda *_: [], raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_quote",
            _request(),
            {"symbol": "INFY", "market": "IN"},
        )
    )

    assert result.ok is False
    assert result.error_code == "quote_unavailable"
    session.close()
    engine.dispose()


def test_research_candidates_tool_reuses_strategy_signals_and_returns_compact_candidates(
    monkeypatch, recommendations_enabled
):
    engine, session = _session()
    captured = {}

    def _list_strategy_signals(**kwargs):
        captured.update(kwargs)
        return {
            "snapshot_date": "2026-09-13",
            "count": 1,
            "items": [
                {
                    "stock_symbol": "INFY",
                    "stock_market": "IN",
                    "stock_name": "Infosys",
                    "rank_score": 88.5,
                    "action": "buy",
                    "action_label": "Open position",
                    "risk_level": "medium",
                    "risk_level_label": "Medium risk",
                    "source_pool": "market_scan",
                    "source_pool_label": "Market pool",
                    "signal": "Trend improving",
                    "reason": "MAs and price-volume structure improving together",
                    "entry_low": 1780,
                    "entry_high": 1820,
                    "target_price": 1950,
                    "stop_loss": 1710,
                    "invalidation": "below 1710",
                    "payload": {
                        "source_meta": {
                            "quote": {
                                "current_price": 1800,
                                "change_pct": 1.2,
                            }
                        }
                    },
                }
            ],
        }

    monkeypatch.setattr(assistant_tools, "list_strategy_signals", _list_strategy_signals, raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "find_research_candidates",
            _request(),
            {"market": "IN", "holding": "unheld", "min_score": 80, "limit": 3},
        )
    )

    assert result.ok is True
    assert captured == {
        "market": "IN",
        "status": "active",
        "min_score": 80.0,
        "limit": 3,
        "source_pool": "all",
        "holding": "unheld",
        "risk_level": "",
        "include_payload": True,
    }
    assert result.data == {
        "snapshot_date": "2026-09-13",
        "count": 1,
        "items": [
            {
                "symbol": "INFY",
                "market": "IN",
                "name": "Infosys",
                "score": 88.5,
                "action": "Open position",
                "risk": "Medium risk",
                "source": "Market pool",
                "signal": "Trend improving",
                "reason": "MAs and price-volume structure improving together",
                "entry_range": "1780 ~ 1820",
                "target_price": 1950,
                "stop_loss": 1710,
                "invalidation": "below 1710",
                "current_price": 1800,
                "change_pct": 1.2,
            }
        ],
    }
    assert "Infosys" in result.summary
    session.close()
    engine.dispose()


def test_research_candidates_tool_rejects_invalid_filters(recommendations_enabled):
    engine, session = _session()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "find_research_candidates",
            _request(),
            {"market": "JP", "limit": 0},
        )
    )

    assert result.ok is False
    assert result.error_code == "candidate_filter_invalid"
    session.close()
    engine.dispose()


def test_kline_summary_tool_returns_compact_summary(monkeypatch):
    class _Collector:
        def __init__(self, _market):
            pass

        def get_kline_summary(self, symbol):
            return {"symbol": symbol, "trend": "up", "ma5": 10.0, "ma20": 9.0}

    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "KlineCollector", _Collector, raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_kline_summary",
            _request(),
            {"symbol": "INFY", "market": "IN"},
        )
    )

    assert result.ok is True
    assert result.data["trend"] == "up"
    session.close()
    engine.dispose()


def test_news_tool_limits_compact_items(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_news",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                title="Infosys announcement",
                source="eastmoney",
                publish_time="2026-09-12T08:00:00Z",
                url="https://example.test/1",
                importance=2,
            ),
            SimpleNamespace(
                title="Industry update",
                source="xueqiu",
                publish_time="2026-09-12T07:00:00Z",
                url="https://example.test/2",
                importance=1,
            ),
        ],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_stock_news",
            _request(),
            {"symbol": "INFY", "market": "IN", "limit": 1},
        )
    )

    assert result.ok is True
    assert result.data["items"] == [
        {
            "title": "Infosys announcement",
            "source": "eastmoney",
            "published_at": "2026-09-12T08:00:00Z",
            "url": "https://example.test/1",
            "importance": 2,
        }
    ]
    session.close()
    engine.dispose()


def test_create_price_alert_validates_and_persists_rule():
    engine, session = _session()
    session.add(Stock(symbol="INFY", name="Infosys", market="IN"))
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "INFY",
                "market": "IN",
                "direction": "above",
                "target_price": 1800,
            },
        )
    )

    rule = session.query(PriceAlertRule).one()
    assert result.ok is True
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "price", "op": ">=", "value": 1800.0}],
    }
    assert "price is ≥ 1800" in result.summary
    session.close()
    engine.dispose()


def test_create_price_alert_registers_a_known_quote_before_writing_rule(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(
        assistant_tools,
        "md_quote_rows",
        lambda *_: [{"symbol": "WIPRO", "name": "Wipro", "market": "IN"}],
        raising=False,
    )

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "WIPRO",
                "market": "IN",
                "direction": "below",
                "target_price": 40,
            },
        )
    )

    stock = session.query(Stock).one()
    rule = session.query(PriceAlertRule).one()
    assert result.ok is True
    assert result.data["stock_registered"] is True
    assert stock.symbol == "WIPRO"
    assert stock.market == "IN"
    assert stock.name == "Wipro"
    assert rule.stock_id == stock.id
    session.close()
    engine.dispose()


def test_create_price_alert_does_not_write_for_unknown_stock(monkeypatch):
    engine, session = _session()
    monkeypatch.setattr(assistant_tools, "md_quote_rows", lambda *_: [], raising=False)

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "create_price_alert",
            _request(),
            {
                "symbol": "000000",
                "market": "IN",
                "direction": "below",
                "target_price": 1,
            },
        )
    )

    assert result.ok is False
    assert result.error_code == "stock_not_found"
    assert session.query(PriceAlertRule).count() == 0
    session.close()
    engine.dispose()


def test_get_price_alerts_returns_compact_rules_and_supports_symbol_filter():
    engine, session = _session()
    stock = Stock(symbol="INFY", name="Infosys", market="IN")
    other = Stock(symbol="TCS", name="Tata Consultancy Services", market="IN")
    session.add_all([stock, other])
    session.flush()
    session.add_all(
        [
            PriceAlertRule(
                stock_id=stock.id,
                name="Infosys breakout",
                enabled=True,
                condition_group={"op": "and", "items": [{"type": "price", "op": ">=", "value": 1800}]},
                cooldown_minutes=30,
            ),
            PriceAlertRule(
                stock_id=other.id,
                name="Tata Motors pullback",
                enabled=False,
                condition_group={"op": "and", "items": [{"type": "price", "op": "<=", "value": 10}]},
            ),
        ]
    )
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "get_price_alerts",
            _request(),
            {"symbol": "INFY", "market": "IN"},
        )
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["items"] == [
        {
            "rule_id": 1,
            "name": "Infosys breakout",
            "symbol": "INFY",
            "stock_name": "Infosys",
            "market": "IN",
            "enabled": True,
            "direction": "above",
            "target_price": 1800.0,
            "cooldown_minutes": 30,
            "max_triggers_per_day": 3,
            "repeat_mode": "repeat",
        }
    ]
    session.close()
    engine.dispose()


def test_update_price_alert_changes_rule_and_resets_trigger_state():
    engine, session = _session()
    stock = Stock(symbol="INFY", name="Infosys", market="IN")
    session.add(stock)
    session.flush()
    rule = PriceAlertRule(
        stock_id=stock.id,
        name="Old alert",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "price", "op": ">=", "value": 1800}]},
        trigger_count_today=2,
        trigger_date="2026-09-12",
    )
    session.add(rule)
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "update_price_alert",
            _request(),
            {
                "rule_id": rule.id,
                "name": "Infosys pullback alert",
                "enabled": False,
                "direction": "below",
                "target_price": 1700,
                "cooldown_minutes": 45,
            },
        )
    )

    session.refresh(rule)
    assert result.ok is True
    assert rule.name == "Infosys pullback alert"
    assert rule.enabled is False
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "price", "op": "<=", "value": 1700.0}],
    }
    assert rule.cooldown_minutes == 45
    assert rule.trigger_count_today == 0
    assert rule.trigger_date == ""
    session.close()
    engine.dispose()


def test_update_price_alert_returns_controlled_failure_for_unknown_rule():
    engine, session = _session()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "update_price_alert", _request(), {"rule_id": 999, "enabled": False}
        )
    )

    assert result.ok is False
    assert result.error_code == "price_alert_not_found"
    session.close()
    engine.dispose()


def test_delete_price_alert_removes_rule_and_its_hits():
    engine, session = _session()
    stock = Stock(symbol="INFY", name="Infosys", market="IN")
    session.add(stock)
    session.flush()
    rule = PriceAlertRule(stock_id=stock.id, name="Delete me")
    session.add(rule)
    session.flush()
    session.add(
        PriceAlertHit(
            rule_id=rule.id,
            stock_id=stock.id,
            trigger_bucket="202609121300",
            trigger_snapshot={},
        )
    )
    session.commit()

    result = asyncio.run(
        assistant_tools.build_panwatch_tool_registry(session).execute(
            "delete_price_alert", _request(), {"rule_id": rule.id}
        )
    )

    assert result.ok is True
    assert session.query(PriceAlertRule).count() == 0
    assert session.query(PriceAlertHit).count() == 0
    session.close()
    engine.dispose()


def test_research_candidates_tool_is_not_registered_in_research_only_mode():
    """Research-only: the entry-candidate engine is not exposed to the assistant."""
    from src.modules.assistant.tools import build_panwatch_tool_registry

    registry = build_panwatch_tool_registry(MagicMock())
    names = {spec.name for spec in registry.registered_tools()}
    assert "find_research_candidates" not in names
    assert "get_stock_quote" in names
