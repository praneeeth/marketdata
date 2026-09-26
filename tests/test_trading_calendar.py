"""India trading calendar: weekends closed; the NSE holiday list arrives in Phase 3."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.scheduling import trading_calendar as tc

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("day", "open_"),
    [
        (date(2026, 9, 21), True),  # Monday
        (date(2026, 9, 25), True),  # Friday
        (date(2026, 9, 26), False),  # Saturday
        (date(2026, 9, 27), False),  # Sunday
    ],
)
def test_weekdays_trade_weekends_do_not(day: date, open_: bool) -> None:
    assert tc.is_trading_day(MarketCode.IN, day) is open_
    assert tc.any_market_trading_day(day) is open_


def test_market_argument_is_ignored() -> None:
    assert tc.is_trading_day("CN", date(2026, 9, 21)) is True  # legacy callers still work
    assert tc.is_trading_day(None, date(2026, 9, 26)) is False


def test_aware_datetimes_use_the_india_date() -> None:
    # 23:00 UTC on Friday is already Saturday 04:30 in India.
    friday_late_utc = datetime(2026, 9, 25, 23, 0, tzinfo=ZoneInfo("UTC"))
    assert tc.is_trading_day(None, friday_late_utc) is False
    assert tc.is_trading_day(None, datetime(2026, 9, 25, 10, 0)) is True  # naive: taken as-is


def test_default_is_today_in_india(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_now", lambda: datetime(2026, 9, 26, 9, 0, tzinfo=IST))
    assert tc.is_trading_day() is False
    monkeypatch.setattr(tc, "_now", lambda: datetime(2026, 9, 28, 9, 0, tzinfo=IST))
    assert tc.any_market_trading_day() is True


def test_refresh_is_a_no_op_until_phase_3() -> None:
    tc.reset_cache()
    assert tc.refresh_blocking() is True
    assert asyncio.run(tc.refresh()) is True


def test_session_hours() -> None:
    md = MARKETS[MarketCode.IN]
    assert md.is_trading_time(datetime(2026, 9, 21, 9, 15, tzinfo=IST))
    assert md.is_trading_time(datetime(2026, 9, 21, 15, 30, tzinfo=IST))
    assert not md.is_trading_time(datetime(2026, 9, 21, 9, 14, tzinfo=IST))
    assert not md.is_trading_time(datetime(2026, 9, 26, 11, 0, tzinfo=IST))
