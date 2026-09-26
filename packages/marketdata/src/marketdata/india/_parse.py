"""Strict parsing helpers shared by the adapters.

Missing or malformed fields become ``None`` (or raise ``BadResponse`` where a value is
required) instead of being guessed. Floats go through ``str`` so ``1412.95`` stays exact.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from marketdata.india.errors import BadResponse
from marketdata.india.types import IST


def dec(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def req_dec(provider: str, value: Any, field: str) -> Decimal:
    result = dec(value)
    if result is None:
        raise BadResponse(provider, f"missing or invalid {field}")
    return result


CENT = Decimal("0.01")


def price(value: Any) -> Decimal | None:
    """A price from a float-based source (Yahoo), rounded to paise to drop float noise
    such as 735.5999755859375."""
    d = dec(value)
    return None if d is None else d.quantize(CENT)


def pct_change(change: Decimal | None, base: Decimal | None) -> Decimal | None:
    if change is None or base is None or base == 0:
        return None
    return (change / base * 100).quantize(CENT)


def integer(value: Any) -> int | None:
    d = dec(value)
    if d is None or d != d.to_integral_value():
        return None
    return int(d)


def positive_or_none(value: Decimal | None) -> Decimal | None:
    """Brokers send 0 for 'not applicable' (e.g. strike on an equity)."""
    return value if value is not None and value > 0 else None


def aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=IST)


def iso_ts(value: Any) -> datetime | None:
    """Parse ISO-8601 timestamps such as ``2017-12-15T09:15:00+0530``; naive means IST."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace(" ", "T", 1)
    # Python < 3.12 wants a colon in the UTC offset.
    if len(text) >= 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        return aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def epoch_ms(value: Any) -> datetime | None:
    n = integer(value)
    if n is None or n <= 0:
        return None
    return datetime.fromtimestamp(n / 1000, tz=IST)


def iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
