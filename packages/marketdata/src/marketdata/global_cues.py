"""Global market cues: world indices, crude, gold and USD/INR, for context only.

The India fork is India-only for research; these cues are read-only context (e.g. "how
did the US close overnight") for the dashboard and the pre-market brief. Brokers don't
provide them, so they come from a pluggable source. The interim source is free, delayed
Yahoo data, tagged ``UNOFFICIAL_DELAYED``; the owner's decision is to switch to a licensed
feed before any public launch (PLAN decision log, 2026-09-25).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from marketdata.india import _parse as p
from marketdata.india.types import DataQuality


@dataclass(frozen=True)
class CueSpec:
    key: str
    name: str
    group: str  # "US" | "Asia" | "Europe" | "Commodities" | "Currency"
    yahoo: str


GLOBAL_CUES: tuple[CueSpec, ...] = (
    CueSpec("SPX", "S&P 500", "US", "^GSPC"),
    CueSpec("IXIC", "Nasdaq Composite", "US", "^IXIC"),
    CueSpec("DJI", "Dow Jones", "US", "^DJI"),
    CueSpec("N225", "Nikkei 225", "Asia", "^N225"),
    CueSpec("HSI", "Hang Seng", "Asia", "^HSI"),
    CueSpec("FTSE", "FTSE 100", "Europe", "^FTSE"),
    CueSpec("DAX", "DAX", "Europe", "^GDAXI"),
    CueSpec("BRENT", "Brent crude (USD/bbl)", "Commodities", "BZ=F"),
    CueSpec("GOLD", "Gold (USD/oz)", "Commodities", "GC=F"),
    CueSpec("USDINR", "USD/INR", "Currency", "INR=X"),
)


@dataclass(frozen=True)
class GlobalCue:
    key: str
    name: str
    group: str
    source: str
    quality: DataQuality
    last: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    as_of: datetime | None = None


class GlobalCuesSource(Protocol):
    name: str
    quality: DataQuality

    def fetch(self, specs: Sequence[CueSpec]) -> list[GlobalCue]: ...


def _missing(spec: CueSpec, source: str, quality: DataQuality) -> GlobalCue:
    return GlobalCue(spec.key, spec.name, spec.group, source, quality)


class YahooGlobalCues:
    """Free, delayed Yahoo data via yfinance. One failing symbol never hides the others."""

    name = "yahoo"
    quality = DataQuality.UNOFFICIAL_DELAYED

    def __init__(
        self,
        ticker_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ticker = ticker_factory or _yfinance_ticker
        self._clock = clock

    def fetch(self, specs: Sequence[CueSpec]) -> list[GlobalCue]:
        return [self._one(spec) for spec in specs]

    def _one(self, spec: CueSpec) -> GlobalCue:
        try:
            info = self._ticker(spec.yahoo).fast_info
            last = p.dec(info["last_price"])
            prev = p.positive_or_none(p.dec(info["previous_close"]))
        # yfinance raises arbitrary types (HTTP, parsing, KeyError on missing fields). A
        # cue is context only, so any failure shows as "no data" for that one cue.
        except Exception:  # noqa: BLE001
            return _missing(spec, self.name, self.quality)
        if last is None:
            return _missing(spec, self.name, self.quality)
        change = last - prev if prev is not None else None
        return GlobalCue(
            spec.key,
            spec.name,
            spec.group,
            self.name,
            self.quality,
            last=last,
            change=change,
            change_pct=p.pct_change(change, prev),
            as_of=self._clock(),
        )


def _yfinance_ticker(symbol: str) -> Any:
    import yfinance

    return yfinance.Ticker(symbol)


class GlobalCuesService:
    """Caches cues for ``ttl`` and keeps the last good value of a cue if a refresh fails."""

    def __init__(
        self,
        source: GlobalCuesSource,
        *,
        specs: Sequence[CueSpec] = GLOBAL_CUES,
        ttl: timedelta = timedelta(seconds=60),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._source = source
        self._specs = tuple(specs)
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._fetched_at: datetime | None = None
        self._cues: dict[str, GlobalCue] = {}

    @property
    def quality(self) -> DataQuality:
        return self._source.quality

    @property
    def source_name(self) -> str:
        return self._source.name

    def cues(self) -> list[GlobalCue]:
        with self._lock:
            now = self._clock()
            if self._fetched_at is None or now - self._fetched_at >= self._ttl:
                fresh = {c.key: c for c in self._source.fetch(self._specs)}
                for key, cue in fresh.items():
                    old = self._cues.get(key)
                    # Keep the last good value rather than blanking a cue on a hiccup.
                    self._cues[key] = cue if cue.last is not None or old is None else old
                self._fetched_at = now
            return [
                self._cues.get(s.key) or _missing(s, self._source.name, self._source.quality)
                for s in self._specs
            ]


__all__ = [
    "GLOBAL_CUES",
    "CueSpec",
    "GlobalCue",
    "GlobalCuesService",
    "GlobalCuesSource",
    "YahooGlobalCues",
]
