"""Unit tests for simulation notifications."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.modules.paper_trading.paper_trading_engine import (
    _serialize_position,
    _serialize_signal,
    _serialize_trade,
)
from src.modules.paper_trading.paper_trading_notifier import (
    _dedup_signals,
    _format_entry_message,
    _format_exit_message,
    _format_premarket_plan,
    _format_daily_summary,
    _strategy_label,
)


def _make_signal(**kwargs):
    """Create a mock StrategySignalRun ORM object."""
    defaults = {
        "id": 1,
        "stock_symbol": "INFY",
        "stock_market": "IN",
        "stock_name": "Infosys",
        "strategy_code": "trend_follow",
        "rank_score": 100.0,
        "entry_low": 112.01,
        "entry_high": 114.27,
        "action": "buy",
        "stop_loss": 108.0,
        "target_price": 125.0,
        "status": "active",
        "snapshot_date": "2026-04-16",
        "created_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_position(**kwargs):
    """Create a mock PaperTradingPosition ORM object."""
    defaults = {
        "id": 1,
        "stock_symbol": "INFY",
        "stock_market": "IN",
        "stock_name": "Infosys",
        "quantity": 100,
        "entry_price": 113.0,
        "stop_loss": 104.0,
        "target_price": 130.0,
        "current_price": 115.0,
        "unrealized_pnl": 200.0,
        "status": "open",
        "strategy_code": "trend_follow",
        "signal_run_id": 1,
        "signal_snapshot_date": "2026-04-16",
        "signal_action": "buy",
        "opened_at": None,
        "closed_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trade(**kwargs):
    """Create a mock PaperTradingTrade ORM object."""
    defaults = {
        "id": 1,
        "stock_symbol": "INFY",
        "stock_market": "IN",
        "stock_name": "Infosys",
        "quantity": 100,
        "entry_price": 113.0,
        "exit_price": 120.0,
        "pnl": 700.0,
        "pnl_pct": 6.19,
        "exit_reason": "target_price",
        "holding_days": 3,
        "strategy_code": "trend_follow",
        "signal_run_id": 1,
        "signal_snapshot_date": "2026-04-16",
        "opened_at": None,
        "closed_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_account(**kwargs):
    defaults = {
        "current_capital": 900000.0,
        "initial_capital": 1000000.0,
        "enabled": True,
        "excluded_markets": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Pre-market plan dedupe tests
# ---------------------------------------------------------------------------


class TestPremarketDedup(unittest.TestCase):
    def test_dedup_same_stock_multiple_strategies(self):
        """Pre-market dedupe: 4 strategies on one stock merge into 1 row."""
        signals = [
            _make_signal(id=1, strategy_code="trend_follow", rank_score=100.0),
            _make_signal(id=2, strategy_code="macd_golden", rank_score=90.0),
            _make_signal(id=3, strategy_code="momentum", rank_score=80.0),
            _make_signal(id=4, strategy_code="market_scan", rank_score=70.0),
        ]
        deduped = _dedup_signals(signals)
        self.assertEqual(len(deduped), 1)
        sig, count = deduped[0]
        self.assertEqual(count, 4)
        self.assertEqual(sig.strategy_code, "trend_follow")  # highest rank_score
        self.assertEqual(sig.rank_score, 100.0)

    def test_dedup_different_stocks(self):
        """Pre-market dedupe: different stocks each stay."""
        signals = [
            _make_signal(id=1, stock_symbol="INFY", rank_score=100.0),
            _make_signal(id=2, stock_symbol="HDFCBANK", stock_name="HDFC Bank", rank_score=95.0),
            _make_signal(id=3, stock_symbol="INFY", strategy_code="macd_golden", rank_score=80.0),
        ]
        deduped = _dedup_signals(signals)
        self.assertEqual(len(deduped), 2)
        # INFY has 2 strategies
        self.assertEqual(deduped[0][1], 2)
        # 000001 has 1 strategy
        self.assertEqual(deduped[1][1], 1)

    def test_premarket_plan_format_with_dedup(self):
        """Pre-market plan: formatted after dedupe, with the strategy count and link."""
        signals = [
            _make_signal(id=1, strategy_code="trend_follow", rank_score=100.0),
            _make_signal(id=2, strategy_code="macd_golden", rank_score=90.0),
            _make_signal(id=3, strategy_code="momentum", rank_score=80.0),
            _make_signal(id=4, strategy_code="market_scan", rank_score=70.0),
        ]
        account = _make_account()
        title, body = _format_premarket_plan(signals, account)

        self.assertIn("[SIMULATION] Pre-market plan", title)
        # The stock appears once (the symbol appears in "INFY.CN" and the URL)
        lines_with_stock = [l for l in body.split("\n") if "INFY" in l]
        self.assertEqual(len(lines_with_stock), 1)
        # Shows the strategy name + count
        self.assertIn("Trend continuation and 3 more", body)
        # Includes the stock link
        self.assertIn("nseindia.com", body)

    def test_premarket_plan_no_signals(self):
        """Pre-market plan: no signals shows no candidates."""
        account = _make_account()
        title, body = _format_premarket_plan([], account)
        self.assertIn("No candidates", body)


# ---------------------------------------------------------------------------
# Message formatting tests (dict input)
# ---------------------------------------------------------------------------


class TestMessageFormat(unittest.TestCase):
    def test_entry_message_format(self):
        """Entry notification: includes price/strategy/link."""
        pos_data = {
            "stock_symbol": "INFY",
            "stock_market": "IN",
            "stock_name": "Infosys",
            "quantity": 100,
            "entry_price": 113.0,
            "stop_loss": 104.0,
            "target_price": 130.0,
            "strategy_code": "trend_follow",
        }
        sig_data = {
            "strategy_code": "trend_follow",
            "rank_score": 100.0,
        }
        title, body = _format_entry_message(pos_data, sig_data)
        self.assertIn("[SIMULATION] Simulated entry", title)
        self.assertIn("Infosys", title)
        self.assertIn("113.00", body)
        self.assertIn("104.00", body)
        self.assertIn("130.00", body)
        self.assertIn("100.0", body)  # rank_score
        self.assertIn("Trend continuation", body)  # strategy name
        self.assertIn("nseindia.com", body)  # stock link

    def test_entry_message_no_signal(self):
        """Entry notification: no error without a signal."""
        pos_data = {
            "stock_symbol": "INFY",
            "stock_market": "IN",
            "stock_name": "Infosys",
            "quantity": 100,
            "entry_price": 113.0,
            "stop_loss": 104.0,
            "target_price": 130.0,
            "strategy_code": "trend_follow",
        }
        title, body = _format_entry_message(pos_data, None)
        self.assertIn("[SIMULATION] Simulated entry", title)
        self.assertIn("Trend continuation", body)

    def test_exit_message_format(self):
        """Exit notification: a profit shows take profit / days held."""
        pos_data = {
            "stock_symbol": "INFY",
            "stock_market": "IN",
            "stock_name": "Infosys",
        }
        trade_data = {
            "entry_price": 113.0,
            "exit_price": 120.0,
            "pnl": 700.0,
            "pnl_pct": 6.19,
            "exit_reason": "target_price",
            "holding_days": 3,
        }
        title, body = _format_exit_message(pos_data, trade_data)
        self.assertIn("[SIMULATION] Simulated exit", title)
        self.assertIn("+700.00", title)
        self.assertIn("Take profit", body)
        self.assertIn("113.00", body)
        self.assertIn("120.00", body)
        self.assertIn("3 days", body)
        self.assertIn("nseindia.com", body)  # stock link

    def test_exit_message_loss(self):
        """Exit notification: a loss shows the minus sign and stop loss."""
        pos_data = {"stock_symbol": "INFY", "stock_market": "IN", "stock_name": "Infosys"}
        trade_data = {
            "entry_price": 113.0,
            "exit_price": 105.0,
            "pnl": -800.0,
            "pnl_pct": -7.08,
            "exit_reason": "stop_loss",
            "holding_days": 1,
        }
        title, body = _format_exit_message(pos_data, trade_data)
        self.assertIn("-800.00", title)
        self.assertIn("Stop loss", body)

    def test_daily_summary_format(self):
        """End-of-day summary: includes total assets / trades closed / open positions."""
        trades = [_make_trade()]
        positions = [_make_position()]
        account = _make_account()
        title, body = _format_daily_summary(trades, positions, account)
        self.assertIn("[SIMULATION] Daily summary", title)
        self.assertIn("Total assets", body)
        self.assertIn("Closed today: 1 trade,", body)
        self.assertIn("Open positions: 1", body)


# ---------------------------------------------------------------------------
# Serialisation tests
# ---------------------------------------------------------------------------


class TestSerialize(unittest.TestCase):
    def test_serialize_position(self):
        """Serialisation: a position object to a dict."""
        pos = _make_position()
        d = _serialize_position(pos)
        self.assertEqual(d["stock_symbol"], "INFY")
        self.assertEqual(d["entry_price"], 113.0)
        self.assertEqual(d["strategy_code"], "trend_follow")
        self.assertIn("id", d)

    def test_serialize_trade(self):
        """Serialisation: a trade record to a dict."""
        trade = _make_trade()
        d = _serialize_trade(trade)
        self.assertEqual(d["exit_price"], 120.0)
        self.assertEqual(d["pnl"], 700.0)
        self.assertEqual(d["exit_reason"], "target_price")

    def test_serialize_signal(self):
        """Serialisation: a strategy signal to a dict."""
        sig = _make_signal()
        d = _serialize_signal(sig)
        self.assertEqual(d["stock_symbol"], "INFY")
        self.assertEqual(d["rank_score"], 100.0)
        self.assertEqual(d["strategy_code"], "trend_follow")


class TestHelpers(unittest.TestCase):
    def test_strategy_label_known(self):
        """Strategy name mapping: known strategies return their display name."""
        self.assertEqual(_strategy_label("trend_follow"), "Trend continuation")
        self.assertEqual(_strategy_label("macd_golden"), "MACD golden cross")
        self.assertEqual(_strategy_label("momentum"), "Momentum")
        self.assertEqual(_strategy_label("market_scan"), "Market scan")

    def test_strategy_label_unknown(self):
        """Strategy name mapping: unknown strategies are returned as is."""
        self.assertEqual(_strategy_label("some_new_strategy"), "some_new_strategy")


if __name__ == "__main__":
    unittest.main()
