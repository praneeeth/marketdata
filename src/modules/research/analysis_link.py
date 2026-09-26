"""Links to PanWatch's own pages, such as the deep research detail page.

Global setting key: panwatch_base_url (the public URL, for absolute detail-page links in notifications).
Read the same way as stock_link.py (AppSettings, falling back to the default on a miss).
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

SETTING_KEY = "panwatch_base_url"


def get_base_url() -> str:
    """Read the public URL from AppSettings (without a trailing slash); empty when unset or the DB is unavailable.

    Guarded: in unit tests or before the DB is initialised (no app_settings table), reading the setting mustn't break the
    whole analysis result mapping; if it can't be read it falls back to an empty string (no detail link).
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
            val = (row.value if row and row.value else "").strip()
            return val.rstrip("/")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — an uninitialised DB / missing table falls back to empty
        logger.debug(f"get_base_url failed; falling back to empty: {e}")
        return ""


def analysis_detail_url(symbol: str, date: str, base_url: str = "") -> str:
    """Deep research detail page URL: {base}/analysis/{symbol}/{date}.

    Returns an empty string when base_url isn't set; callers use that to decide whether to add a link.
    """
    if not base_url:
        base_url = get_base_url()
    if not base_url:
        return ""
    return f"{base_url}/analysis/{symbol}/{date}"


def analysis_detail_markdown(
    symbol: str, date: str, label: str = "📊 View the full analysis", base_url: str = ""
) -> str:
    """Markdown link [label](url); empty string without a base_url."""
    url = analysis_detail_url(symbol, date, base_url)
    if not url:
        return ""
    return f"[{label}]({url})"
