"""Global cues: one failing symbol never hides the rest; cache and last-good values."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from marketdata.global_cues import (
    GLOBAL_CUES,
    CueSpec,
    GlobalCue,
    GlobalCuesService,
    YahooGlobalCues,
)
from marketdata.india.types import DataQuality

NOW = datetime(2026, 9, 25, 2, 0, tzinfo=UTC)

PRICES: dict[str, dict[str, Any]] = {
    "^GSPC": {"last_price": 6612.5, "previous_close": 6580.0},
    "INR=X": {"last_price": 83.21, "previous_close": 83.35},
    "^N225": {"last_price": 38500.0, "previous_close": None},
}


class FakeTicker:
    def __init__(self, symbol: str) -> None:
        if symbol == "^HSI":
            raise ConnectionError("GET https://query1.example?crumb=SECRET")
        self.fast_info = PRICES.get(symbol, {})


def test_every_cue_is_listed_once_in_order() -> None:
    keys = [c.key for c in GLOBAL_CUES]
    assert len(keys) == len(set(keys))
    assert {c.group for c in GLOBAL_CUES} == {"US", "Asia", "Europe", "Commodities", "Currency"}
    assert "USDINR" in keys
    assert "BRENT" in keys


def test_yahoo_source_tolerates_per_symbol_failures() -> None:
    cues = {c.key: c for c in YahooGlobalCues(FakeTicker, clock=lambda: NOW).fetch(GLOBAL_CUES)}
    spx = cues["SPX"]
    assert spx.last == Decimal("6612.5")
    assert spx.change == Decimal("32.5")
    assert spx.change_pct == Decimal("0.49")
    assert spx.as_of == NOW
    assert spx.quality is DataQuality.UNOFFICIAL_DELAYED
    assert cues["USDINR"].change == Decimal("-0.14")
    assert cues["N225"].last == Decimal("38500.0")
    assert cues["N225"].change is None  # no previous close
    assert cues["HSI"].last is None  # network error: just this cue is empty
    assert cues["FTSE"].last is None  # missing fields
    assert len(cues) == len(GLOBAL_CUES)


class CountingSource:
    name = "fake"
    quality = DataQuality.UNOFFICIAL_DELAYED

    def __init__(self) -> None:
        self.calls = 0
        self.values: dict[str, Decimal | None] = {"SPX": Decimal(1)}

    def fetch(self, specs: Sequence[CueSpec]) -> list[GlobalCue]:
        self.calls += 1
        return [
            GlobalCue(s.key, s.name, s.group, self.name, self.quality, last=self.values.get(s.key))
            for s in specs
        ]


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def test_service_caches_for_ttl() -> None:
    src, clock = CountingSource(), Clock()
    svc = GlobalCuesService(src, ttl=timedelta(seconds=60), clock=clock)
    svc.cues()
    svc.cues()
    assert src.calls == 1
    clock.now = NOW + timedelta(seconds=60)
    svc.cues()
    assert src.calls == 2
    assert svc.quality is DataQuality.UNOFFICIAL_DELAYED
    assert svc.source_name == "fake"


def test_service_keeps_last_good_value() -> None:
    src, clock = CountingSource(), Clock()
    svc = GlobalCuesService(src, ttl=timedelta(seconds=1), clock=clock)
    assert svc.cues()[0].last == Decimal(1)
    src.values["SPX"] = None  # refresh fails for SPX
    clock.now = NOW + timedelta(seconds=5)
    assert svc.cues()[0].last == Decimal(1)


def test_service_lists_specs_the_source_skipped() -> None:
    class Partial(CountingSource):
        def fetch(self, specs: Sequence[CueSpec]) -> list[GlobalCue]:
            return super().fetch(specs[:1])

    cues = GlobalCuesService(Partial(), clock=Clock()).cues()
    assert [c.key for c in cues] == [c.key for c in GLOBAL_CUES]
    assert cues[1].last is None


def test_default_ticker_factory_imports_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake = types.ModuleType("yfinance")
    fake.Ticker = lambda symbol: f"ticker:{symbol}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    from marketdata import global_cues

    assert global_cues._yfinance_ticker("^GSPC") == "ticker:^GSPC"
