"""Per-user market data service: failover, per-credential caching, instrument resolution.

Callers pass the *current user's* broker sessions in priority order. The service:

- fails over only among those sessions (it refuses sessions from different users);
- keys every cache entry by ``credential_id``, so data fetched with one user's credential
  is never served to another user (bring your own key, no redistribution);
- resolves each provider's instrument ID through that credential's own instrument master
  (decision Q9: per user until legal sign-off);
- skips sessions that have already expired and reports them, so the UI can say
  "Kite session expired, reconnect" instead of failing silently.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TypeVar

from marketdata.cache import TTLCache
from marketdata.errors import MarketDataError
from marketdata.india.errors import (
    BadResponse,
    InstrumentNotResolved,
    NotSupported,
    ProviderError,
    SessionExpired,
)
from marketdata.india.instrument_cache import InstrumentCache
from marketdata.india.provider import MarketDataProvider
from marketdata.india.session import ProviderSession
from marketdata.india.types import (
    IST,
    Candle,
    Capability,
    CorporateAction,
    InstrumentRef,
    Interval,
    OptionChain,
    Quote,
)

T = TypeVar("T")


@dataclass(frozen=True)
class CacheTTLs:
    quote_s: float = 5.0
    intraday_candles_s: float = 30.0
    daily_candles_s: float = 600.0
    option_chain_s: float = 15.0
    corporate_actions_s: float = 3600.0
    instruments: timedelta = timedelta(hours=12)


class AllProvidersFailed(MarketDataError):
    """No connected provider could answer. ``failures`` explains each one."""

    def __init__(self, what: str, failures: Sequence[tuple[str, ProviderError]]) -> None:
        self.failures = tuple(failures)
        detail = "; ".join(str(err) for _name, err in self.failures) or "no provider connected"
        super().__init__(f"{what} unavailable: {detail}")

    @property
    def needs_reconnect(self) -> list[str]:
        """Providers whose session expired; the user must log in to them again."""
        return [name for name, err in self.failures if isinstance(err, SessionExpired)]


class IndiaMarketData:
    def __init__(
        self,
        providers: Mapping[str, MarketDataProvider],
        *,
        ttls: CacheTTLs | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(IST),
        cache: TTLCache | None = None,
        instrument_cache: InstrumentCache | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._ttls = ttls or CacheTTLs()
        self._clock = clock
        self._cache = cache or TTLCache(max_size=4096)
        self._instruments = instrument_cache or InstrumentCache(self._ttls.instruments, clock)

    # --- public API --------------------------------------------------------------------

    def quotes(
        self, sessions: Sequence[ProviderSession], refs: Sequence[InstrumentRef]
    ) -> list[Quote]:
        if not refs:
            return []

        def fetch(provider: MarketDataProvider, session: ProviderSession) -> list[Quote]:
            key = self._key(session, "quotes", *(r.display for r in refs))
            cached: list[Quote] | None = self._cache.get(key)
            if cached is not None:
                return cached
            # Skip symbols this provider doesn't list (e.g. delisted) instead of failing
            # the whole batch; fail over only if none of them can be resolved.
            resolved: list[InstrumentRef] = []
            for ref in refs:
                try:
                    resolved.append(self._resolve(provider, session, ref))
                except InstrumentNotResolved:
                    continue
            if not resolved:
                raise InstrumentNotResolved(
                    provider.name, "none of the instruments are in its instrument list"
                )
            quotes = provider.quotes(session, resolved)
            if not quotes:
                raise BadResponse(provider.name, "returned no quotes")
            self._cache.set(key, quotes, self._ttls.quote_s)
            return quotes

        return self._failover("quotes", sessions, Capability.QUOTES, fetch)

    def candles(
        self,
        sessions: Sequence[ProviderSession],
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        needed = Capability.INTRADAY if interval.is_intraday else Capability.OHLC
        ttl = self._ttls.intraday_candles_s if interval.is_intraday else self._ttls.daily_candles_s

        def fetch(provider: MarketDataProvider, session: ProviderSession) -> list[Candle]:
            key = self._key(
                session, "candles", ref.display, interval.value, start.isoformat(), end.isoformat()
            )
            cached: list[Candle] | None = self._cache.get(key)
            if cached is not None:
                return cached
            resolved = self._resolve(provider, session, ref)
            candles = provider.candles(session, resolved, interval, start, end)
            self._cache.set(key, candles, ttl)
            return candles

        return self._failover("candles", sessions, needed, fetch)

    def corporate_actions(
        self,
        sessions: Sequence[ProviderSession],
        ref: InstrumentRef,
        start: date,
        end: date,
    ) -> list[CorporateAction]:
        def fetch(provider: MarketDataProvider, session: ProviderSession) -> list[CorporateAction]:
            key = self._key(session, "corp", ref.display, start.isoformat(), end.isoformat())
            cached: list[CorporateAction] | None = self._cache.get(key)
            if cached is not None:
                return cached
            actions = provider.corporate_actions(
                session, self._resolve(provider, session, ref), start, end
            )
            self._cache.set(key, actions, self._ttls.corporate_actions_s)
            return actions

        return self._failover("corporate actions", sessions, Capability.CORP_ACTIONS, fetch)

    def option_chain(
        self, sessions: Sequence[ProviderSession], underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        def fetch(provider: MarketDataProvider, session: ProviderSession) -> OptionChain:
            key = self._key(session, "chain", underlying.display, expiry.isoformat())
            cached: OptionChain | None = self._cache.get(key)
            if cached is not None:
                return cached
            resolved = self._resolve(provider, session, underlying, required=False)
            chain = provider.option_chain(session, resolved, expiry)
            self._cache.set(key, chain, self._ttls.option_chain_s)
            return chain

        return self._failover("option chain", sessions, Capability.OPTION_CHAIN, fetch)

    def forget_credential(self, credential_id: str) -> None:
        """Drop the instrument master of a disconnected credential.

        Quote/candle entries for it expire on their own within seconds to minutes.
        """
        self._instruments.forget(credential_id)

    # --- internals -----------------------------------------------------------------------

    def _failover(
        self,
        what: str,
        sessions: Sequence[ProviderSession],
        needed: Capability,
        fetch: Callable[[MarketDataProvider, ProviderSession], T],
    ) -> T:
        users = {s.user_id for s in sessions}
        if len(users) > 1:
            raise ValueError("failover across different users' credentials is not allowed")
        now = self._clock()
        failures: list[tuple[str, ProviderError]] = []
        for session in sessions:
            provider = self._providers.get(session.provider)
            if provider is None:
                failures.append(
                    (session.provider, NotSupported(session.provider, "provider not configured"))
                )
                continue
            if needed not in provider.capabilities:
                continue
            if session.is_expired(now):
                failures.append(
                    (provider.name, SessionExpired(provider.name, "session expired, reconnect"))
                )
                continue
            try:
                return fetch(provider, session)
            except ProviderError as e:
                failures.append((provider.name, e))
        raise AllProvidersFailed(what, failures)

    def _resolve(
        self,
        provider: MarketDataProvider,
        session: ProviderSession,
        ref: InstrumentRef,
        *,
        required: bool = True,
    ) -> InstrumentRef:
        if ref.id_for(provider.name) or Capability.INSTRUMENTS not in provider.capabilities:
            return ref
        index = self._instruments.get(
            session, ref.exchange, lambda: provider.instruments(session, ref.exchange)
        )
        inst = index.get(ref.tradingsymbol.upper())
        provider_id = inst.ref.id_for(provider.name) if inst else None
        if provider_id:
            return ref.with_id(provider.name, provider_id)
        if required:
            raise InstrumentNotResolved(
                provider.name, f"{ref.display} is not in {provider.name}'s instrument list"
            )
        return ref

    @staticmethod
    def _key(session: ProviderSession, kind: str, *parts: str) -> str:
        # The credential ID is always part of the key: no cross-user cache hits.
        return "|".join((session.credential_id, session.provider, kind, *parts))
