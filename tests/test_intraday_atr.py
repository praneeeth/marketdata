"""Tests for ATR-adaptive unusual move detection.

Covers:
1. the atr / atr_pct fields of kline_collector._calculate_atr / get_technical_indicators
2. intraday_event_gate.is_abnormal_move adaptive detection (with the fixed-threshold floor)
3. intraday_monitor.build_prompt injecting ATR and the adaptive move rule text
"""

from __future__ import annotations

from src.platform.marketdata.collectors.kline_collector import (
    KlineData,
    _calculate_atr,
)


def _mk(o: float, h: float, l: float, c: float) -> KlineData:
    """Build one K-line bar (volume fixed at 1000; doesn't affect ATR)."""
    return KlineData(date="2026-06-20", open=o, high=h, low=l, close=c, volume=1000)


def test_calculate_atr_known_series() -> None:
    """ATR matches a hand-computed value on a small known OHLC sample (simple mean of TR)."""
    # 6 bars, period=3.
    # close series: 10, 11, 10, 12, 11, 13
    klines = [
        _mk(10, 10.5, 9.5, 10.0),  # first bar (no previous close; no TR)
        _mk(10, 12.0, 10.0, 11.0),  # TR = max(2, |12-10|, |10-10|) = 2
        _mk(11, 11.5, 9.0, 10.0),  # TR = max(2.5, |11.5-11|, |9-11|) = 2.5
        _mk(10, 12.5, 10.0, 12.0),  # TR = max(2.5, |12.5-10|, |10-10|) = 2.5
        _mk(12, 12.0, 10.5, 11.0),  # TR = max(1.5, |12-12|, |10.5-12|) = 1.5
        _mk(11, 13.5, 11.0, 13.0),  # TR = max(2.5, |13.5-11|, |11-11|) = 2.5
    ]
    # period=3 -> the last 3 TRs: [2.5, 1.5, 2.5] -> mean = 2.1666...
    atr = _calculate_atr(klines, period=3)
    assert atr is not None
    assert abs(atr - (2.5 + 1.5 + 2.5) / 3) < 1e-9


def test_calculate_atr_insufficient_data_returns_none() -> None:
    """ATR returns None without raising when there are too few bars (<= period)."""
    klines = [_mk(10, 11, 9, 10), _mk(10, 11, 9, 10)]
    # period+1 bars are needed to compute period TRs
    assert _calculate_atr(klines, period=14) is None
    assert _calculate_atr([], period=14) is None
    # Exactly period bars isn't enough either (only period-1 TRs)
    assert _calculate_atr(klines, period=2) is None


def test_get_technical_indicators_includes_atr_and_pct() -> None:
    """Technical indicators: get_technical_indicators returns atr and atr_pct (= atr / close * 100)."""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector

    # The default ATR period is 14, so at least 15 bars are needed.
    klines = [_mk(10 + i * 0.1, 11 + i * 0.1, 9 + i * 0.1, 10 + i * 0.1) for i in range(14)]
    klines.append(_mk(11, 13.5, 11.0, 20.0))  # latest close = 20, to make atr_pct easy to check
    collector = KlineCollector(MarketStub())
    ind = collector.get_technical_indicators(klines=klines)
    assert ind.atr is not None
    assert ind.atr_pct is not None
    # atr_pct = round(atr / latest_close * 100, 2)
    assert abs(ind.atr_pct - round(ind.atr / 20.0 * 100, 2)) < 1e-9


def test_get_technical_indicators_atr_none_when_insufficient() -> None:
    """Technical indicators: with too few bars atr / atr_pct are None, but the other indicators still return."""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector

    klines = [_mk(10, 11, 9, 10)]
    collector = KlineCollector(MarketStub())
    ind = collector.get_technical_indicators(klines=klines)
    assert ind.atr is None
    assert ind.atr_pct is None


class MarketStub:
    """KlineCollector only uses market when fetching over the network; this test passes klines, so a placeholder is fine."""

    def __init__(self) -> None:
        from src.platform.marketdata.models import MarketCode

        self.value = MarketCode.IN


# ── is_abnormal_move (adaptive unusual move detection) ──────────────────────


def test_is_abnormal_move_beyond_k_times_atr() -> None:
    """Adaptive: a change beyond k x ATR% is unusual."""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # atr_pct=2, k=1.5 -> threshold 3.0; change=4 is unusual
    assert is_abnormal_move(change_pct=4.0, atr_pct=2.0, k=1.5) is True
    assert is_abnormal_move(change_pct=-4.0, atr_pct=2.0, k=1.5) is True


def test_is_abnormal_move_within_band_is_normal() -> None:
    """Adaptive: a change within k x ATR% is normal."""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # atr_pct=2, k=1.5 -> threshold 3.0; change=2 is within the band
    assert is_abnormal_move(change_pct=2.0, atr_pct=2.0, k=1.5) is False
    assert is_abnormal_move(change_pct=-2.5, atr_pct=2.0, k=1.5) is False


def test_is_abnormal_move_falls_back_to_fixed_threshold() -> None:
    """Adaptive: atr_pct None/0 falls back to the fixed threshold."""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # No ATR -> the fixed threshold 3.0
    assert is_abnormal_move(change_pct=4.0, atr_pct=None, fixed_threshold=3.0) is True
    assert is_abnormal_move(change_pct=2.0, atr_pct=None, fixed_threshold=3.0) is False
    # atr_pct=0 falls back too
    assert is_abnormal_move(change_pct=4.0, atr_pct=0.0, fixed_threshold=3.0) is True
    assert is_abnormal_move(change_pct=2.0, atr_pct=0.0, fixed_threshold=3.0) is False


def test_is_abnormal_move_default_k() -> None:
    """Adaptive: k has a default (1.5)."""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # default k=1.5, atr_pct=2 -> threshold 3.0
    assert is_abnormal_move(change_pct=3.5, atr_pct=2.0) is True
    assert is_abnormal_move(change_pct=2.5, atr_pct=2.0) is False


# ── intraday_monitor build_prompt injects ATR / the adaptive rule ───────────


def _build_intraday_prompt_with_atr(atr_pct):
    """Build a minimal AgentContext/data, run build_prompt and return user_content."""
    from src.modules.automation.intraday_monitor import IntradayMonitorAgent
    from src.platform.marketdata.models import MarketCode, StockData

    stock = StockData(
        symbol="000001",
        name="HDFC Bank",
        market=MarketCode.IN,
        current_price=20.0,
        change_pct=5.0,
        change_amount=1.0,
        open_price=19.0,
        high_price=20.5,
        low_price=18.8,
        prev_close=19.0,
        volume=10000,
        turnover=200000,
    )

    class _Portfolio:
        total_available_funds = 100000.0
        accounts: list = []

        def get_positions_for_stock(self, symbol):
            return []

    class _Ctx:
        portfolio = _Portfolio()

    data = {
        "stock_data": stock,
        "kline_summary": {"atr": 0.6, "atr_pct": atr_pct, "trend": "bullish alignment"},
        "symbol_context": {},
    }
    agent = IntradayMonitorAgent(price_alert_threshold=3.0)
    _system, user_content = agent.build_prompt(data, _Ctx())
    return user_content


def test_build_prompt_injects_atr_and_adaptive_rule() -> None:
    """Intraday monitor: the prompt injects ATR% and the adaptive price move threshold text."""
    content = _build_intraday_prompt_with_atr(atr_pct=2.0)
    # The ATR value appears in the technical summary
    assert "ATR" in content
    # Adaptive threshold: max(fixed 3.0, 1.5 x ATR% = 3.0) -> the text mentions adapting
    assert "adapts" in content
    # change_pct=5.0 exceeds the adaptive threshold -> marked as triggered
    assert "triggered" in content


def test_build_prompt_falls_back_when_atr_missing() -> None:
    """Intraday monitor: with atr_pct missing it falls back to the fixed threshold, and the prompt still builds."""
    content = _build_intraday_prompt_with_atr(atr_pct=None)
    # The fixed threshold is still shown
    assert "3.0%" in content or "3.0" in content
    # change_pct=5.0 > fixed threshold 3.0 -> triggered
    assert "triggered" in content
