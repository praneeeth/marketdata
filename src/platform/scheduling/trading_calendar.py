"""Trading calendar: "is the market open on this day?" (NSE/BSE, India-only).

Complements ``MarketDef.is_trading_time()`` ("is it inside a session right now?").
Scheduled jobs such as the pre-market brief run outside sessions, so they guard on the
trading *day* instead.

Until Phase 3 adds the NSE holiday and special-session calendar (dates must be copied
from NSE's official circular, open question Q11), only weekends count as closed. Erring
towards "open" means at worst one extra notification, never a silently skipped day.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Kolkata")


def reset_cache() -> None:
    """Kept for callers and tests; there is no cached calendar until Phase 3."""


def refresh_blocking() -> bool:
    """No remote calendar to load yet (Phase 3). Always succeeds."""
    return True


async def refresh() -> bool:
    return refresh_blocking()


def _now() -> datetime:
    """Current time in India. Separate so tests can patch it."""
    return datetime.now(_TZ)


def _resolve_date(d: date | datetime | None) -> date:
    if d is None:
        return _now().date()
    if isinstance(d, datetime):
        return (d.astimezone(_TZ) if d.tzinfo is not None else d).date()
    return d


def is_trading_day(market: object = None, d: date | datetime | None = None) -> bool:
    """Whether NSE/BSE trade on ``d`` (default: today in India).

    ``market`` is accepted for compatibility with upstream callers and ignored: India is
    the only market.
    """
    return _resolve_date(d).weekday() < 5


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    return is_trading_day(None, d)
