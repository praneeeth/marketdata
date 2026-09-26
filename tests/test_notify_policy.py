"""tests for src/core/notify_policy.py"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.platform.notifications.notify_policy import NotifyPolicy, _parse_hhmm, parse_dedupe_overrides


# ---------------------------------------------------------------------------
# _parse_hhmm
# ---------------------------------------------------------------------------

class TestParseHHMM:
    def test_normal(self):
        """Time parsing: normal format 09:30."""
        assert _parse_hhmm("09:30") == time(9, 30)

    def test_single_digit_hour(self):
        """Time parsing: single-digit hour 0:05."""
        assert _parse_hhmm("0:05") == time(0, 5)

    def test_midnight(self):
        """Time parsing: 23:59 boundary."""
        assert _parse_hhmm("23:59") == time(23, 59)

    def test_invalid_hour(self):
        """Time parsing: invalid hour 25 raises."""
        with pytest.raises(ValueError):
            _parse_hhmm("25:00")

    def test_invalid_minute(self):
        """Time parsing: invalid minute 60 raises."""
        with pytest.raises(ValueError):
            _parse_hhmm("12:60")

    def test_negative_hour(self):
        """Time parsing: a negative hour raises."""
        with pytest.raises(ValueError):
            _parse_hhmm("-1:00")


# ---------------------------------------------------------------------------
# is_quiet_now
# ---------------------------------------------------------------------------

class TestIsQuietNow:
    def test_empty_quiet_hours(self):
        """Quiet hours: empty config returns False."""
        p = NotifyPolicy(quiet_hours="")
        assert p.is_quiet_now() is False

    def test_normal_range_inside(self):
        """Quiet hours: 23:00 is inside 22:00-06:00."""
        p = NotifyPolicy(timezone="Asia/Kolkata", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert p.is_quiet_now(now) is True

    def test_normal_range_outside(self):
        """Quiet hours: 12:00 is outside 22:00-06:00."""
        p = NotifyPolicy(timezone="Asia/Kolkata", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert p.is_quiet_now(now) is False

    def test_crosses_midnight_before(self):
        """Quiet hours: across midnight, 23:30 is inside."""
        p = NotifyPolicy(timezone="UTC", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_crosses_midnight_after(self):
        """Quiet hours: across midnight, 03:00 is inside."""
        p = NotifyPolicy(timezone="UTC", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 2, 3, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_same_start_end_always_quiet(self):
        """Quiet hours: equal start and end means quiet all day."""
        p = NotifyPolicy(timezone="UTC", quiet_hours="08:00-08:00")
        now = datetime(2024, 1, 1, 15, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_non_crossing_boundary_start(self):
        """Quiet hours: exactly the start time is inside."""
        p = NotifyPolicy(timezone="UTC", quiet_hours="09:00-17:00")
        now = datetime(2024, 1, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_non_crossing_boundary_end(self):
        """Quiet hours: exactly the end time is outside."""
        p = NotifyPolicy(timezone="UTC", quiet_hours="09:00-17:00")
        now = datetime(2024, 1, 1, 17, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is False

    def test_invalid_format(self):
        """Quiet hours: an invalid format returns False."""
        p = NotifyPolicy(quiet_hours="invalid")
        assert p.is_quiet_now() is False


# ---------------------------------------------------------------------------
# parse_dedupe_overrides
# ---------------------------------------------------------------------------

class TestParseDedupeOverrides:
    def test_normal(self):
        """Dedupe override parsing: normal JSON."""
        assert parse_dedupe_overrides('{"agent_a": 10, "agent_b": 20}') == {
            "agent_a": 10,
            "agent_b": 20,
        }

    def test_empty_string(self):
        """Dedupe override parsing: empty string returns an empty dict."""
        assert parse_dedupe_overrides("") == {}

    def test_none(self):
        """Dedupe override parsing: None returns an empty dict."""
        assert parse_dedupe_overrides(None) == {}

    def test_invalid_json(self):
        """Dedupe override parsing: invalid JSON returns an empty dict."""
        assert parse_dedupe_overrides("not json") == {}

    def test_non_dict_json(self):
        """Dedupe override parsing: a non-dict returns an empty dict."""
        assert parse_dedupe_overrides("[1,2,3]") == {}

    def test_non_int_values_skipped(self):
        """Dedupe override parsing: non-integer values are skipped."""
        result = parse_dedupe_overrides('{"a": 10, "b": "not_int"}')
        assert result == {"a": 10}


# ---------------------------------------------------------------------------
# dedupe_ttl_minutes
# ---------------------------------------------------------------------------

class TestDedupeTTLMinutes:
    def test_hit(self):
        """TTL lookup: hits the override config."""
        p = NotifyPolicy(dedupe_ttl_overrides={"agent_a": 30})
        assert p.dedupe_ttl_minutes("agent_a", default=10) == 30

    def test_miss(self):
        """TTL lookup: a miss returns the default."""
        p = NotifyPolicy(dedupe_ttl_overrides={"agent_a": 30})
        assert p.dedupe_ttl_minutes("agent_b", default=10) == 10

    def test_none_overrides(self):
        """TTL lookup: no override config returns the default."""
        p = NotifyPolicy(dedupe_ttl_overrides=None)
        assert p.dedupe_ttl_minutes("agent_a", default=5) == 5
