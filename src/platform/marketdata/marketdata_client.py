"""Market data entry points used across the app (India-only).

Quotes come from the current user's broker connections through the India bridge
(:mod:`src.platform.marketdata.india_bridge`). The upstream Chinese/HK/US scraping vendors
were removed in Phase 2. The ``market`` arguments are kept so upstream call sites keep
working; India is the only market.

News functions return nothing until Indian news and filings sources arrive in Phase 4a
(open question Q8 covers the sourcing decision).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def md_quote_rows(symbols: list[str], market: str = "IN") -> list[dict[str, Any]]:
    """Batch quotes as dict rows (upstream shape plus ``source``/``quality``). Sync.

    Async callers use ``await asyncio.to_thread(md_quote_rows, ...)``.
    """
    syms = list(symbols)
    if not syms:
        return []
    from src.platform.marketdata.india_bridge import get_india_bridge

    return get_india_bridge().quote_rows(syms)


def md_stock_data(symbols: list[str], market: str = "IN") -> list[Any]:
    """Quotes as ``StockData`` objects (upstream collector shape). Sync."""
    from src.platform.marketdata.models import MarketCode, StockData

    return [
        StockData(
            symbol=r["symbol"],
            name=r["name"] or "",
            market=MarketCode.IN,
            current_price=r["current_price"] or 0.0,
            change_pct=r["change_pct"] or 0.0,
            change_amount=r["change_amount"] or 0.0,
            volume=r["volume"] or 0.0,
            turnover=0.0,
            open_price=r["open_price"] or 0.0,
            high_price=r["high_price"] or 0.0,
            low_price=r["low_price"] or 0.0,
            prev_close=r["prev_close"] or 0.0,
        )
        for r in md_quote_rows(symbols)
    ]


def md_news(
    symbols: list[str], since_hours: int = 2, names: dict[str, str] | None = None
) -> list[Any]:
    """Stock news and announcements. Empty until Indian sources land (Phase 4a)."""
    return []


def md_news_by_keyword(keyword: str) -> list[Any]:
    """Keyword news search. Empty until Indian sources land (Phase 4a)."""
    return []


# Headline Indian indices: (symbol as the bridge understands it, display name).
INDIA_INDICES: tuple[tuple[str, str], ...] = (
    ("NIFTY 50", "NIFTY 50"),
    ("NIFTY BANK", "NIFTY BANK"),
    ("BSE:SENSEX", "SENSEX"),
)


def md_india_indices() -> list[Any]:
    """NIFTY 50, NIFTY BANK and SENSEX as ``IndexData`` (empty if no broker answers). Sync."""
    from datetime import datetime

    from src.platform.marketdata.models import IndexData, MarketCode

    rows = {r["symbol"]: r for r in md_quote_rows([s for s, _ in INDIA_INDICES])}
    out = []
    for symbol, name in INDIA_INDICES:
        r = rows.get(symbol)
        if not r:
            continue
        out.append(
            IndexData(
                symbol=symbol,
                name=name,
                market=MarketCode.IN,
                current_price=r["current_price"] or 0.0,
                change_pct=r["change_pct"] or 0.0,
                change_amount=r["change_amount"] or 0.0,
                volume=r["volume"] or 0.0,
                turnover=0.0,
                timestamp=datetime.now(),
            )
        )
    return out
