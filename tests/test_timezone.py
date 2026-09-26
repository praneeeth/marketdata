"""Tests for src/platform/scheduling/timezone.py."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.platform.scheduling.timezone import (
    format_local,
    to_iso_utc,
    to_iso_with_tz,
    to_local,
    to_utc,
)


class TestToUtc:
    def test_aware_datetime(self):
        """To UTC: an aware datetime converts correctly."""
        dt = datetime(2024, 1, 15, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        result = to_utc(dt)
        assert result.tzinfo == timezone.utc
        assert (result.hour, result.minute) == (4, 30)  # Kolkata is UTC+5:30

    def test_naive_datetime_treated_as_app_tz(self, monkeypatch):
        """To UTC: a naive datetime is treated as the app time zone."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        dt = datetime(2024, 1, 15, 10, 0)
        result = to_utc(dt)
        assert result.tzinfo == timezone.utc
        assert (result.hour, result.minute) == (4, 30)


class TestToLocal:
    def test_utc_to_local(self, monkeypatch):
        """To local time: UTC 02:00 -> 07:30 IST."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        dt = datetime(2024, 1, 15, 2, 0, tzinfo=timezone.utc)
        result = to_local(dt)
        assert (result.hour, result.minute) == (7, 30)

    def test_naive_treated_as_utc(self, monkeypatch):
        """To local time: a naive datetime is treated as UTC."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        dt = datetime(2024, 1, 15, 2, 0)
        result = to_local(dt)
        assert (result.hour, result.minute) == (7, 30)


class TestFormatLocal:
    def test_default_format(self, monkeypatch):
        """Formatting: default format YYYY-MM-DD HH:MM:SS."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        dt = datetime(2024, 1, 15, 2, 30, 0, tzinfo=timezone.utc)
        result = format_local(dt)
        assert result == "2024-01-15 08:00:00"

    def test_custom_format(self, monkeypatch):
        """Formatting: custom format HH:MM."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        dt = datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
        result = format_local(dt, fmt="%H:%M")
        assert result == "07:30"


class TestToIsoUtc:
    def test_utc_input(self):
        """ISO UTC: UTC input gets a Z suffix."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert to_iso_utc(dt) == "2024-01-15T10:30:00Z"

    def test_non_utc_input(self):
        """ISO UTC: non-UTC input is converted."""
        dt = datetime(2024, 1, 15, 16, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert to_iso_utc(dt) == "2024-01-15T10:30:00Z"


class TestToIsoWithTz:
    def test_aware(self):
        """ISO with time zone: keeps the original offset."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = to_iso_with_tz(dt)
        assert "10:30:00" in result
        assert "+00:00" in result

    def test_naive_gets_utc(self):
        """ISO with time zone: a naive datetime defaults to UTC."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = to_iso_with_tz(dt)
        assert "+00:00" in result
