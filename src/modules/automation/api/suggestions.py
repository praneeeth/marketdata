"""Suggestion pool API."""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.modules.automation.suggestion_pool import (
    get_suggestions_for_stock,
    get_latest_suggestions,
    cleanup_expired_suggestions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{symbol}")
def get_stock_suggestions(
    symbol: str,
    market: str = Query("", description="Market: IN (NSE/BSE)"),
    include_expired: bool = Query(False, description="Include expired items"),
    limit: int = Query(10, description="Maximum number of items"),
    db: Session = Depends(get_db),
):
    """
    All items for one stock.

    Returns the stock's items, newest first.
    """
    suggestions = get_suggestions_for_stock(
        stock_symbol=symbol,
        stock_market=(market or "").strip().upper() or None,
        include_expired=include_expired,
        limit=limit,
    )
    return suggestions


@router.get("/", name="get_suggestions")
@router.get("", include_in_schema=False)  # also handle the path without a trailing slash
def get_all_latest_suggestions(
    symbols: str = Query(None, description="Stock symbols, comma-separated"),
    stock_keys: str = Query(
        None, description="Market:symbol list, e.g. IN:INFY,IN:TCS"
    ),
    include_expired: bool = Query(False, description="Include expired items"),
    db: Session = Depends(get_db),
):
    """
    Latest item for every stock.

    Only the newest valid item per stock,
    for showing each stock's latest item quickly on the holdings page.
    """
    symbol_list = None
    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    key_list = None
    if stock_keys:
        parsed: list[tuple[str, str]] = []
        for part in stock_keys.split(","):
            text = (part or "").strip()
            if not text:
                continue
            if ":" not in text:
                parsed.append((text.strip().upper(), "IN"))
                continue
            market, symbol = text.split(":", 1)
            mkt = (market or "IN").strip().upper() or "IN"
            sym = (symbol or "").strip().upper()
            if sym:
                parsed.append((sym, mkt))
        key_list = parsed or None

    suggestions = get_latest_suggestions(
        stock_symbols=symbol_list,
        stock_keys=key_list,
        include_expired=include_expired,
    )
    return suggestions


@router.delete("/cleanup")
def cleanup_suggestions(
    days: int = Query(7, description="Delete rows older than this many days"),
    db: Session = Depends(get_db),
):
    """
    Delete expired items.

    Deletes rows older than 7 days by default.
    """
    count = cleanup_expired_suggestions(days=days)
    return {"deleted": count}
