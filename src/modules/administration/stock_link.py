"""External quote-page links for a stock, on the platform the user chose in Settings.

Setting key: ``stock_link_platform`` (default ``nse``). Symbols are India-only:
``"INFY"`` means NSE, ``"BSE:500209"`` means BSE, as stored by the watchlist.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

PLATFORMS = {
    "nse": "NSE India",
    "tradingview": "TradingView",
    "google": "Google Finance",
}

DEFAULT_PLATFORM = "nse"
SETTING_KEY = "stock_link_platform"


def get_platform() -> str:
    """The platform chosen in Settings; unknown or legacy values fall back to NSE."""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
        value = row.value if row and row.value else DEFAULT_PLATFORM
        return value if value in PLATFORMS else DEFAULT_PLATFORM
    finally:
        db.close()


def _split(symbol: str) -> tuple[str, str]:
    """``"BSE:500209"`` -> ("BSE", "500209"); ``"INFY"`` -> ("NSE", "INFY")."""
    text = symbol.strip().upper()
    prefix, sep, rest = text.partition(":")
    if sep and prefix in ("NSE", "BSE"):
        return prefix, rest.strip()
    return "NSE", text


def stock_url(symbol: str, market: str = "IN", platform: str = "") -> str:
    """Quote-page URL for ``symbol``. ``market`` is kept for callers; India is the only one."""
    exchange, code = _split(symbol)
    platform = platform if platform in PLATFORMS else get_platform()
    if platform == "tradingview":
        return f"https://www.tradingview.com/symbols/{exchange}-{quote(code, safe='')}/"
    if platform == "google" or exchange == "BSE":
        # NSE's site has no BSE pages; Google Finance covers both (BOM = BSE).
        suffix = "NSE" if exchange == "NSE" else "BOM"
        return f"https://www.google.com/finance/quote/{quote(code, safe='')}:{suffix}"
    return f"https://www.nseindia.com/get-quotes/equity?symbol={quote(code, safe='')}"


def stock_link_markdown(symbol: str, market: str = "IN", platform: str = "") -> str:
    """Markdown link such as ``[INFY](https://www.nseindia.com/get-quotes/equity?symbol=INFY)``."""
    return f"[{symbol}]({stock_url(symbol, market, platform)})"
