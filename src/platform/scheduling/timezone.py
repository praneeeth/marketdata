"""Time zone helpers: uniform time storage and display.

The default time zone can be overridden with environment variables:
- TZ (recommended)

Defaults to Asia/Kolkata (India-only fork).
"""

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo


def _get_app_tz() -> ZoneInfo:
    tz_name = os.environ.get("TZ") or os.environ.get("APP_TIMEZONE") or "Asia/Kolkata"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    """Current UTC time (time-zone aware)."""
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """Current time in the default time zone (time-zone aware)."""
    return datetime.now(_get_app_tz())


def to_utc(dt: datetime) -> datetime:
    """Convert a time to UTC."""
    if dt.tzinfo is None:
        # A naive time is assumed to be in the default time zone
        dt = dt.replace(tzinfo=_get_app_tz())
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime) -> datetime:
    """Convert a time to the default time zone."""
    if dt.tzinfo is None:
        # A naive time is assumed to be UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_get_app_tz())


def format_local(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format as a string in the default time zone."""
    return to_local(dt).strftime(fmt)


def to_iso_utc(dt: datetime) -> str:
    """Convert to an ISO UTC string (with a Z suffix)."""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_with_tz(dt: datetime) -> str:
    """Convert to an ISO string (with the time-zone offset)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
