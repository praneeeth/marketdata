"""Unit tests for the TradingAgents history comparison module."""

from __future__ import annotations

from src.modules.automation.tradingagents.operations import (
    _classify_hit,
    _compute_stats,
    _find_close_after_n_trading_days,
    _find_close_on_or_after,
)


def test_classify_hit_buy_up_hits():
    """A buy decision followed by a rise -> hit=True."""
    assert _classify_hit("buy", 5.0) is True


def test_classify_hit_buy_down_misses():
    """A buy decision followed by a fall -> hit=False."""
    assert _classify_hit("buy", -3.0) is False


def test_classify_hit_sell_down_hits():
    """A sell decision followed by a fall -> hit=True."""
    assert _classify_hit("sell", -2.5) is True


def test_classify_hit_hold_flat_hits():
    """A hold decision with a flat move (|x|<2%) -> hit=True."""
    assert _classify_hit("hold", 1.5) is True
    assert _classify_hit("hold", -1.0) is True


def test_classify_hit_hold_big_move_misses():
    """A hold decision with a big move -> hit=False."""
    assert _classify_hit("hold", 5.0) is False


def test_classify_hit_unknown_action_returns_none():
    """An unknown action returns None."""
    assert _classify_hit("xyz", 5.0) is None


def test_classify_hit_no_data_returns_none():
    """No later return data -> None."""
    assert _classify_hit("buy", None) is None


def test_find_close_on_or_after_exact_match():
    """The target date is a trading day -> returned directly."""
    klines = {"2026-05-15": 10.5, "2026-05-16": 10.7}
    result = _find_close_on_or_after(klines, "2026-05-15")
    assert result == ("2026-05-15", 10.5)


def test_find_close_on_or_after_weekend_skips_to_monday():
    """The target date is a weekend/holiday -> find the next trading day."""
    klines = {"2026-05-15": 10.5, "2026-05-18": 11.0}  # the 16th/17th are a weekend
    result = _find_close_on_or_after(klines, "2026-05-16")
    assert result == ("2026-05-18", 11.0)


def test_find_close_on_or_after_no_match_returns_none():
    """No trading day found within 7 days -> None."""
    klines = {"2026-01-01": 10.0}
    result = _find_close_on_or_after(klines, "2026-05-16")
    assert result is None


def test_find_close_after_n_trading_days():
    """Find the close N trading days after the base date."""
    sorted_dates = ["2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21"]
    klines = {d: 10.0 + i for i, d in enumerate(sorted_dates)}
    # 3 trading days after 5-15 = 5-20 -> close=13.0
    assert _find_close_after_n_trading_days(sorted_dates, "2026-05-15", 3, klines) == 13.0


def test_find_close_after_n_trading_days_overflow_returns_none():
    """N beyond the available range -> None."""
    sorted_dates = ["2026-05-15", "2026-05-18"]
    klines = {"2026-05-15": 10.0, "2026-05-18": 11.0}
    assert _find_close_after_n_trading_days(sorted_dates, "2026-05-15", 10, klines) is None


def test_compute_stats_empty_returns_zero_total():
    """Empty list -> total=0."""
    assert _compute_stats([])["total"] == 0


def test_compute_stats_hit_rates_per_action():
    """Hit rate per action."""
    items = [
        {"action": "buy", "return_20d_pct": 5.0, "hit_20d": True},
        {"action": "buy", "return_20d_pct": -2.0, "hit_20d": False},
        {"action": "sell", "return_20d_pct": -3.0, "hit_20d": True},
        {"action": "hold", "return_20d_pct": 0.5, "hit_20d": True},
    ]
    stats = _compute_stats(items)
    assert stats["total"] == 4
    assert stats["buy_count"] == 2
    assert stats["sell_count"] == 1
    assert stats["hold_count"] == 1
    assert stats["buy_hit_rate"] == 0.5
    assert stats["sell_hit_rate"] == 1.0
    assert stats["hold_hit_rate"] == 1.0
    assert stats["overall_hit_rate"] == 0.75
    # (5-2-3+0.5)/4 = 0.125; Python's round() uses banker's rounding to even -> 0.12
    assert stats["avg_return_20d_pct"] == 0.12


def test_compute_stats_skips_items_without_20d_return():
    """Latest decisions younger than 20 days aren't counted."""
    items = [
        {"action": "buy", "return_20d_pct": None, "hit_20d": None},  # just happened
        {"action": "buy", "return_20d_pct": 5.0, "hit_20d": True},
    ]
    stats = _compute_stats(items)
    assert stats["total"] == 2
    assert stats["buy_hit_rate"] == 1.0  # based only on the 2nd row
