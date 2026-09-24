"""IndiaMarketData: per-user failover, credential-isolated caching, instrument resolution."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from marketdata.india import (
    IST,
    BadResponse,
    Candle,
    Capability,
    CorporateAction,
    CorporateActionKind,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentNotResolved,
    InstrumentRef,
    InstrumentType,
    Interval,
    NotSupported,
    OptionChain,
    ProviderSession,
    ProviderUnavailable,
    Quote,
    RateLimited,
    Secret,
    SessionExpired,
)
from marketdata.india.service import AllProvidersFailed, CacheTTLs, IndiaMarketData

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=IST)
INFY = InstrumentRef(Exchange.NSE, "INFY")
ALL_CAPS = frozenset(Capability)


class FakeProvider:
    quality = DataQuality.OFFICIAL_REALTIME

    def __init__(
        self,
        name: str,
        *,
        fail: Exception | None = None,
        capabilities: frozenset[Capability] = ALL_CAPS,
        price: str = "100",
    ) -> None:
        self.name = name
        self.capabilities = capabilities
        self.fail = fail
        self.price = Decimal(price)
        self.calls: list[tuple[str, str]] = []
        self.seen_refs: list[InstrumentRef] = []

    def _enter(self, what: str, session: ProviderSession) -> None:
        self.calls.append((what, session.credential_id))
        if self.fail is not None:
            raise self.fail

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        self._enter("quotes", session)
        self.seen_refs.extend(refs)
        return [
            Quote(r, self.price, self.name, self.quality)
            for r in refs
            if r.tradingsymbol != "EMPTY"
        ]

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        self._enter("candles", session)
        self.seen_refs.append(ref)
        return [Candle(start, self.price, self.price, self.price, self.price, 1)]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        self._enter("instruments", session)
        yield Instrument(
            exchange,
            "INFY",
            "INFOSYS",
            InstrumentType.EQUITY,
            "NSE",
            1,
            Decimal("0.05"),
            provider_ids=((self.name, f"{self.name}-408065"),),
        )

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        self._enter("corp", session)
        return [CorporateAction(ref, CorporateActionKind.DIVIDEND, start, self.name, self.quality)]

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        self._enter("chain", session)
        self.seen_refs.append(underlying)
        return OptionChain(underlying, expiry, self.name, self.quality)


def sess(
    provider: str, credential_id: str, user_id: str = "alice", expires: datetime | None = None
) -> ProviderSession:
    return ProviderSession(
        provider=provider,
        credential_id=credential_id,
        user_id=user_id,
        access_token=Secret("t"),
        expires_at=expires,
    )


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def service(*providers: FakeProvider, clock: Clock | None = None) -> IndiaMarketData:
    return IndiaMarketData({p.name: p for p in providers}, clock=clock or Clock())


def test_resolves_ids_through_the_users_own_instrument_master() -> None:
    kite = FakeProvider("kite")
    svc = service(kite)
    (q,) = svc.quotes([sess("kite", "c-alice")], [INFY])
    assert kite.seen_refs[0].id_for("kite") == "kite-408065"
    assert q.last_price == 100
    assert kite.calls == [("instruments", "c-alice"), ("quotes", "c-alice")]


def test_cache_never_crosses_credentials() -> None:
    kite = FakeProvider("kite")
    svc = service(kite)
    svc.quotes([sess("kite", "c-alice")], [INFY])
    svc.quotes([sess("kite", "c-alice")], [INFY])  # cache hit
    svc.quotes([sess("kite", "c-bob", user_id="bob")], [INFY])  # other user: fetched again
    assert kite.calls.count(("quotes", "c-alice")) == 1
    assert kite.calls.count(("quotes", "c-bob")) == 1
    # Instrument masters are per credential too (Q9).
    assert kite.calls.count(("instruments", "c-bob")) == 1


def test_instrument_master_expires() -> None:
    clock = Clock()
    kite = FakeProvider("kite")
    svc = service(kite, clock=clock)
    ref2 = InstrumentRef(Exchange.NSE, "infy")  # lookup is case-insensitive
    svc.quotes([sess("kite", "c1")], [INFY])
    svc.candles([sess("kite", "c1")], ref2, Interval.DAY_1, NOW, NOW)
    assert kite.calls.count(("instruments", "c1")) == 1
    clock.now = NOW + CacheTTLs().instruments
    svc.candles([sess("kite", "c1")], INFY, Interval.MINUTE_5, NOW, NOW)
    assert kite.calls.count(("instruments", "c1")) == 2


def test_forget_credential_drops_its_master() -> None:
    kite = FakeProvider("kite")
    svc = service(kite)
    svc.candles([sess("kite", "c1")], INFY, Interval.DAY_1, NOW, NOW)
    svc.forget_credential("c1")
    svc.candles([sess("kite", "c1")], INFY, Interval.DAY_1, NOW, NOW + timedelta(days=1))
    assert kite.calls.count(("instruments", "c1")) == 2


@pytest.mark.parametrize(
    "error",
    [
        SessionExpired("kite", "expired"),
        RateLimited("kite", "slow down"),
        ProviderUnavailable("kite", "down"),
        BadResponse("kite", "odd"),
    ],
)
def test_fails_over_to_the_same_users_next_provider(error: Exception) -> None:
    kite = FakeProvider("kite", fail=error)
    upstox = FakeProvider("upstox", price="101")
    svc = service(kite, upstox)
    (q,) = svc.quotes([sess("kite", "k1"), sess("upstox", "u1")], [INFY])
    assert q.source == "upstox"
    assert q.last_price == 101


def test_refuses_to_mix_users() -> None:
    svc = service(FakeProvider("kite"), FakeProvider("upstox"))
    with pytest.raises(ValueError, match="different users"):
        svc.quotes([sess("kite", "k1", "alice"), sess("upstox", "u1", "bob")], [INFY])


def test_expired_sessions_are_skipped_and_reported() -> None:
    kite = FakeProvider("kite")
    svc = service(kite)
    with pytest.raises(AllProvidersFailed) as exc:
        svc.quotes([sess("kite", "k1", expires=NOW - timedelta(minutes=1))], [INFY])
    assert exc.value.needs_reconnect == ["kite"]
    assert "session expired" in str(exc.value)
    assert kite.calls == []


def test_all_failed_lists_every_reason() -> None:
    svc = service(
        FakeProvider("kite", fail=SessionExpired("kite", "token")),
        FakeProvider("upstox", fail=ProviderUnavailable("upstox", "HTTP 503")),
    )
    with pytest.raises(AllProvidersFailed) as exc:
        svc.quotes([sess("kite", "k1"), sess("upstox", "u1"), sess("angel", "a1")], [INFY])
    assert [name for name, _ in exc.value.failures] == ["kite", "upstox", "angel"]
    assert exc.value.needs_reconnect == ["kite"]
    assert "provider not configured" in str(exc.value)


def test_no_sessions() -> None:
    with pytest.raises(AllProvidersFailed, match="no provider connected"):
        service().quotes([], [INFY])
    assert service().quotes([], []) == []


def test_capabilities_route_requests() -> None:
    no_intraday = FakeProvider("kite", capabilities=ALL_CAPS - {Capability.INTRADAY})
    other = FakeProvider("upstox")
    svc = service(no_intraday, other)
    sessions = [sess("kite", "k1"), sess("upstox", "u1")]
    svc.candles(sessions, INFY, Interval.MINUTE_5, NOW, NOW)
    assert ("candles", "k1") not in no_intraday.calls
    svc.candles(sessions, INFY, Interval.DAY_1, NOW, NOW)
    assert ("candles", "k1") in no_intraday.calls


def test_unresolvable_instrument_fails_over() -> None:
    kite = FakeProvider("kite")
    upstox = FakeProvider("upstox")
    svc = service(kite, upstox)
    with pytest.raises(AllProvidersFailed) as exc:
        svc.quotes(
            [sess("kite", "k1"), sess("upstox", "u1")], [InstrumentRef(Exchange.NSE, "NOPE")]
        )
    assert all(isinstance(e, InstrumentNotResolved) for _, e in exc.value.failures)


def test_empty_quote_result_fails_over() -> None:
    kite = FakeProvider("kite")
    empty = InstrumentRef(Exchange.NSE, "EMPTY").with_id("kite", "1")
    with pytest.raises(AllProvidersFailed, match="no quotes"):
        service(kite).quotes([sess("kite", "k1")], [empty])


def test_providers_without_instrument_master_get_refs_as_is() -> None:
    yf = FakeProvider("yfinance", capabilities=ALL_CAPS - {Capability.INSTRUMENTS})
    svc = service(yf)
    svc.quotes([sess("yfinance", "y1")], [INFY])
    assert yf.seen_refs == [INFY]
    assert all(c[0] != "instruments" for c in yf.calls)


def test_corporate_actions_and_option_chain_are_cached_per_credential() -> None:
    kite = FakeProvider("kite")
    svc = service(kite)
    s = [sess("kite", "k1")]
    for _ in range(2):
        svc.corporate_actions(s, INFY, date(2026, 1, 1), date(2026, 12, 31))
        chain = svc.option_chain(s, InstrumentRef(Exchange.NSE, "NIFTY 50"), date(2026, 10, 27))
    assert kite.calls.count(("corp", "k1")) == 1
    assert kite.calls.count(("chain", "k1")) == 1
    # The underlying need not be in the master for chains (brokers build them themselves).
    assert chain.underlying.id_for("kite") is None
    svc.candles(s, INFY, Interval.DAY_1, NOW, NOW)
    svc.candles(s, INFY, Interval.DAY_1, NOW, NOW)
    assert kite.calls.count(("candles", "k1")) == 1


def test_option_chain_failover_to_capable_provider() -> None:
    no_chain = FakeProvider("kite", capabilities=ALL_CAPS - {Capability.OPTION_CHAIN})
    with pytest.raises(AllProvidersFailed):
        service(no_chain).option_chain([sess("kite", "k1")], INFY, date(2026, 10, 27))
    unsupported = FakeProvider("kite", fail=NotSupported("kite", "no"))
    with pytest.raises(AllProvidersFailed, match="no"):
        service(unsupported).corporate_actions(
            [sess("kite", "k1")], INFY, date(2026, 1, 1), date(2026, 2, 1)
        )
