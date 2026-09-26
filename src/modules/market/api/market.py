"""Headline Indian indices for the dashboard: NIFTY 50, NIFTY BANK, SENSEX.

Served from the logged-in user's broker connection (their own data, so the route needs
login). Caching happens per credential in the India data layer, never across users.
"""

import asyncio
import logging

from fastapi import APIRouter

from src.platform.marketdata.collectors.kline_collector import get_index_klines
from src.platform.marketdata.marketdata_client import INDIA_INDICES, md_quote_rows

logger = logging.getLogger(__name__)
router = APIRouter()


def _spark_for(symbol: str) -> list[float]:
    """Last 20 daily closes for the sparkline; empty on any failure."""
    try:
        return [k.close for k in get_index_klines(symbol, days=20)]
    except Exception as e:  # noqa: BLE001 - a missing sparkline must not fail the quotes
        logger.debug("index sparkline unavailable %s: %s", symbol, e)
        return []


@router.get("/indices")
async def get_market_indices():
    symbols = [s for s, _ in INDIA_INDICES]
    rows_task = asyncio.to_thread(md_quote_rows, symbols)
    spark_tasks = [asyncio.to_thread(_spark_for, s) for s in symbols]
    rows, *sparks = await asyncio.gather(rows_task, *spark_tasks, return_exceptions=True)
    quotes = {r["symbol"]: r for r in rows} if isinstance(rows, list) else {}
    result = []
    for (symbol, name), spark in zip(INDIA_INDICES, sparks):
        q = quotes.get(symbol) or {}
        result.append({
            "symbol": symbol,
            "name": name,
            "market": "IN",
            "current_price": q.get("current_price"),
            "change_pct": q.get("change_pct"),
            "change_amount": q.get("change_amount"),
            "prev_close": q.get("prev_close"),
            "spark": spark if isinstance(spark, list) else [],
        })
    return result
