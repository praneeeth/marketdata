"""InstrumentCache: per-credential TTL cache with single-flight loading (review #7, #8)."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from marketdata.india import IST, Exchange, Instrument, InstrumentType, ProviderSession
from marketdata.india.instrument_cache import InstrumentCache

NOW = datetime(2026, 9, 23, 10, tzinfo=IST)


def inst(symbol: str) -> Instrument:
    return Instrument(
        Exchange.NSE, symbol, symbol, InstrumentType.EQUITY, "NSE", 1, Decimal("0.05")
    )


def sess(cred: str) -> ProviderSession:
    return ProviderSession(provider="kite", credential_id=cred, user_id="u")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def test_loads_once_per_credential_and_exchange_until_ttl() -> None:
    clock = Clock()
    cache = InstrumentCache(timedelta(hours=12), clock)
    calls: list[str] = []

    def loader(tag: str):  # type: ignore[no-untyped-def]
        def load():  # type: ignore[no-untyped-def]
            calls.append(tag)
            return [inst("INFY"), inst("tcs")]

        return load

    index = cache.get(sess("c1"), Exchange.NSE, loader("c1"))
    assert set(index) == {"INFY", "TCS"}  # keys are upper-cased
    cache.get(sess("c1"), Exchange.NSE, loader("c1"))
    cache.get(sess("c2"), Exchange.NSE, loader("c2"))  # other credential: its own copy
    cache.get(sess("c1"), Exchange.NFO, loader("c1-nfo"))
    assert calls == ["c1", "c2", "c1-nfo"]
    clock.now = NOW + timedelta(hours=12)
    cache.get(sess("c1"), Exchange.NSE, loader("c1-again"))
    assert calls[-1] == "c1-again"


def test_forget_drops_only_that_credential() -> None:
    cache = InstrumentCache(timedelta(hours=12), Clock())
    calls: list[str] = []
    for cred in ("c1", "c2"):
        cache.get(sess(cred), Exchange.NSE, lambda cred=cred: calls.append(cred) or [inst("A")])
    cache.forget("c1")
    for cred in ("c1", "c2"):
        cache.get(sess(cred), Exchange.NSE, lambda cred=cred: calls.append(cred) or [inst("A")])
    assert calls == ["c1", "c2", "c1"]


def test_concurrent_misses_download_once() -> None:
    cache = InstrumentCache(timedelta(hours=12), Clock())
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_loader():  # type: ignore[no-untyped-def]
        calls.append(1)
        started.set()
        release.wait(5)
        return [inst("INFY")]

    results: list[dict[str, Instrument]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(cache.get(sess("c1"), Exchange.NSE, slow_loader))
        )
        for _ in range(8)
    ]
    threads[0].start()
    started.wait(5)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert len(results) == 8
    assert all("INFY" in r for r in results)


def test_failed_load_is_not_cached() -> None:
    cache = InstrumentCache(timedelta(hours=12), Clock())
    attempts: list[int] = []

    def flaky():  # type: ignore[no-untyped-def]
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("download failed")
        return [inst("INFY")]

    with pytest.raises(RuntimeError, match="download failed"):
        cache.get(sess("c1"), Exchange.NSE, flaky)
    assert "INFY" in cache.get(sess("c1"), Exchange.NSE, flaky)
    assert len(attempts) == 2
