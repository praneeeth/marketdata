"""Per-credential instrument master cache with single-flight loading.

Instrument masters are large (Kite's NFO CSV has tens of thousands of rows; Angel's scrip
master is tens of MB), so each one is downloaded at most once per (credential, exchange)
per TTL. Concurrent misses for the same key wait for one download instead of each
starting their own. Caching per credential follows decision Q9 (no sharing across users
until legal sign-off). A failed download is not cached.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from marketdata.india.session import ProviderSession
from marketdata.india.types import Exchange, Instrument

Key = tuple[str, Exchange]
Index = dict[str, Instrument]


class InstrumentCache:
    def __init__(self, ttl: timedelta, clock: Callable[[], datetime]) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Key, tuple[datetime, Index]] = {}
        self._loading: dict[Key, threading.Lock] = {}

    def get(
        self,
        session: ProviderSession,
        exchange: Exchange,
        load: Callable[[], Iterable[Instrument]],
    ) -> Index:
        """The master for this credential and exchange, keyed by upper-cased symbol."""
        key = (session.credential_id, exchange)
        cached = self._fresh(key)
        if cached is not None:
            return cached
        with self._lock:
            key_lock = self._loading.setdefault(key, threading.Lock())
        with key_lock:
            cached = self._fresh(key)  # another thread may have loaded it meanwhile
            if cached is not None:
                return cached
            index = {i.tradingsymbol.upper(): i for i in load()}
            with self._lock:
                self._entries[key] = (self._clock(), index)
            return index

    def forget(self, credential_id: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == credential_id]:
                del self._entries[key]

    def _fresh(self, key: Key) -> Index | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None or self._clock() - entry[0] >= self._ttl:
            return None
        return entry[1]
