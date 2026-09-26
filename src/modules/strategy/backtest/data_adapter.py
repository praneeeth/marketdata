"""Back-test data adapter: KlineCollector -> PriceBar, aligned to the trading calendar.

- PriceBar is defined at the top of this module, and KlineCollector is **not imported at the top** (lazy import),
  so the back-test core and unit tests aren't coupled to httpx/network libraries and can run offline.
- KlineCollector already returns adjusted daily bars; suspended days naturally have no bar, so the trading calendar = the actual bar series.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBar:
    """One daily bar (adjusted)."""

    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def from_klines(klines) -> list[PriceBar]:
    """KlineData list -> PriceBar list in ascending date order."""
    out: list[PriceBar] = []
    for k in klines or []:
        try:
            out.append(
                PriceBar(
                    date=str(k.date)[:10],
                    open=float(k.open),
                    high=float(k.high),
                    low=float(k.low),
                    close=float(k.close),
                    volume=float(k.volume or 0),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def load_price_history(symbol: str, market, days: int = 250) -> list[PriceBar]:
    """Fetch history through KlineCollector (lazy import, so the network library isn't coupled at the top)."""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode

    try:
        mc = market if isinstance(market, MarketCode) else MarketCode(str(market).upper())
    except Exception:
        mc = MarketCode.IN
    try:
        klines = KlineCollector(mc).get_klines(symbol, days=days)
    except Exception as e:
        logger.warning(f"[Back-test] K-line fetch for {symbol} failed: {e}")
        return []
    return from_klines(klines)


def first_index_after(bars: list[PriceBar], date: str) -> int | None:
    """Index of the first bar whose date is strictly after the given date (the next trading day, against look-ahead)."""
    for i, b in enumerate(bars):
        if b.date > date:
            return i
    return None
