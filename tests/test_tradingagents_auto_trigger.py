"""Unit tests for TradingAgents linked triggering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.modules.automation.tradingagents import operations as auto_trigger


def _make_agent(raw_config: dict):
    agent = MagicMock()
    agent.raw_config = raw_config
    return agent


def test_no_change_pct_skips():
    """Change % missing -> no trigger."""
    ok, reason = auto_trigger.should_auto_trigger("601238", None)
    assert ok is False
    assert "No change %" in reason


def test_disabled_in_config_skips():
    """auto_trigger.enabled=false -> no trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": False, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "is off" in reason


def test_no_agent_config_skips():
    """tradingagents agent not registered -> no trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = None
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False


def test_below_threshold_skips():
    """Change % below the threshold -> no trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        # query(AgentConfig) returns the agent the first time
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 3.0)
    assert ok is False
    assert "below the threshold" in reason


def test_above_threshold_within_cooldown_skips():
    """Threshold reached but already triggered within 24h -> no trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=True), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0, "cooldown_hours": 24}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "Cooling down" in reason


def test_above_threshold_budget_exceeded_skips():
    """Threshold reached but the monthly budget is used up -> no trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=False):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "budget" in reason


def test_above_threshold_all_pass_triggers():
    """Threshold reached + not cooling down + budget left -> trigger."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is True
    assert "reached the threshold" in reason


def test_negative_change_pct_uses_abs():
    """A fall of 8% should trigger too (uses |change_pct|)."""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, _ = auto_trigger.should_auto_trigger("601238", -8.0)
    assert ok is True


def test_try_auto_trigger_returns_none_when_disabled():
    """try_auto_trigger returns None when the conditions aren't met."""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.modules.automation.tradingagents.operations.should_auto_trigger", return_value=(False, "test")):
        result = auto_trigger.try_auto_trigger(stock)
    assert result is None


def test_try_auto_trigger_fires_when_should():
    """try_auto_trigger calls fire_and_forget_trigger when the conditions are met."""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.modules.automation.tradingagents.operations.should_auto_trigger", return_value=(True, "test")), \
         patch("src.modules.automation.tradingagents.operations.fire_and_forget_trigger", return_value="trace-abc") as fire:
        result = auto_trigger.try_auto_trigger(stock)
    assert result == "trace-abc"
    fire.assert_called_once()
