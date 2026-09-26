"""KlineCollector.get_kline_summary builds a summary with English status labels."""

from __future__ import annotations

from src.platform.marketdata.collectors.kline_collector import KlineCollector, KlineData
from src.platform.marketdata.models import MarketCode


def _bars(n: int = 70) -> list[KlineData]:
    out = []
    for i in range(n):
        close = 100.0 + i  # steady uptrend
        out.append(
            KlineData(
                date=f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000 + i,
            )
        )
    return out


def test_summary_builds_with_computed_at_and_english_labels(monkeypatch) -> None:
    collector = KlineCollector(MarketCode.IN)
    monkeypatch.setattr(collector, "get_klines", lambda symbol, days=60: _bars())

    summary = collector.get_kline_summary("INFY")

    assert "error" not in summary
    assert summary["computed_at"]
    assert summary["trend"] == "bullish alignment"


def test_summary_without_klines_reports_no_data(monkeypatch) -> None:
    collector = KlineCollector(MarketCode.IN)
    monkeypatch.setattr(collector, "get_klines", lambda symbol, days=60: [])

    assert collector.get_kline_summary("INFY") == {"error": "No K-line data"}
