"""A price alert's volume ratio condition should use the quote field first instead of fetching K-lines needlessly (batch cleanup P2).

Quotes that carry a volume ratio use it; only when the quote lacks one does it fall back to the K-line summary.
"""

from __future__ import annotations

import asyncio

from src.modules.market.price_alert_engine import PriceAlertEngine
from src.platform.marketdata.models import MarketCode


def test_volume_ratio_uses_quote_not_kline(monkeypatch):
    """With a volume ratio in the quote, the condition uses the quote directly without fetching K-lines."""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 9.9}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 10.0, "volume_ratio": 2.5}
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.IN,
            "600519",
        )
    )

    assert ok is True
    assert detail["actual"] == 2.5
    assert called["kline"] == 0, "no K-line fetch when the quote has a volume ratio"


def test_volume_ratio_falls_back_to_kline_when_quote_missing(monkeypatch):
    """Without a volume ratio in the quote, the condition falls back to the K-line summary."""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 3.0}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 200.0}  # no volume_ratio field
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.IN,
            "AAPL",
        )
    )

    assert ok is True
    assert detail["actual"] == 3.0
    assert called["kline"] == 1, "should fall back to K-lines once when the quote lacks a volume ratio"
