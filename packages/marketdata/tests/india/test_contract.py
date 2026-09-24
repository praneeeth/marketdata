"""Contract suite: every India provider must pass these, offline, against doc fixtures.

Fixtures are hand-written from provider docs, not recorded from live traffic (see
``fixtures/README.md``).
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from marketdata.india import (
    Capability,
    InstrumentNotResolved,
    Interval,
    MarketDataProvider,
    NotSupported,
    ProviderError,
    SessionExpired,
)

from .harness import LEAK_CANARY, Harness, session_for
from .providers import ALL

pytestmark = pytest.mark.parametrize("h", ALL, ids=[h.name for h in ALL])

_ORDER_WORDS = (
    "order",
    "place",
    "modify",
    "cancel",
    "gtt",
    "position",
    "holding",
    "fund",
    "margin",
)


def test_implements_protocol_read_only(h: Harness) -> None:
    provider, _ = h.build()
    assert isinstance(provider, MarketDataProvider)
    assert provider.name == h.name
    public = [n for n, _ in inspect.getmembers(provider) if not n.startswith("_")]
    assert not [n for n in public if any(w in n.lower() for w in _ORDER_WORDS)]


def test_quotes(h: Harness) -> None:
    provider, _ = h.build()
    quotes = provider.quotes(session_for(h.name), [h.equity])
    assert len(quotes) == 1
    q = quotes[0]
    assert q.instrument.tradingsymbol == h.equity.tradingsymbol
    assert q.last_price == Decimal(h.expected_last_price)
    assert q.source == h.name
    assert q.quality is provider.quality
    for ts in (q.exchange_ts, q.last_trade_ts):
        assert ts is None or ts.tzinfo is not None


def test_quotes_empty_input(h: Harness) -> None:
    provider, router = h.build()
    assert provider.quotes(session_for(h.name), []) == []
    assert router.requests == []


def test_candles_sorted_in_range_and_aware(h: Harness) -> None:
    provider, _ = h.build()
    candles = provider.candles(session_for(h.name), h.equity, Interval.MINUTE_5, h.start, h.end)
    assert candles
    assert [c.ts for c in candles] == sorted(c.ts for c in candles)
    for c in candles:
        assert c.ts.tzinfo is not None
        assert h.start <= c.ts <= h.end
        assert c.low <= min(c.open, c.close)
        assert c.high >= max(c.open, c.close)


def test_candles_need_resolved_instrument(h: Harness) -> None:
    provider, router = h.build()
    with pytest.raises(InstrumentNotResolved):
        provider.candles(session_for(h.name), h.unresolved, Interval.DAY_1, h.start, h.end)
    assert router.requests == []


def test_instruments_carry_provider_ids(h: Harness) -> None:
    provider, _ = h.build()
    items = list(provider.instruments(session_for(h.name), h.equity.exchange))
    assert items
    for inst in items:
        assert inst.ref.id_for(h.name)
        assert inst.lot_size >= 1
        assert inst.tick_size >= 0
    assert h.equity.tradingsymbol in {i.tradingsymbol for i in items}


def test_corporate_actions_match_capability(h: Harness) -> None:
    provider, _ = h.build()
    if Capability.CORP_ACTIONS in provider.capabilities:
        pytest.skip("covered by provider-specific tests")
    with pytest.raises(NotSupported):
        provider.corporate_actions(session_for(h.name), h.equity, h.start.date(), h.end.date())


def test_option_chain(h: Harness) -> None:
    provider, _ = h.build()
    if Capability.OPTION_CHAIN not in provider.capabilities:
        pytest.skip("provider has no option chain")
    chain = provider.option_chain(session_for(h.name), h.underlying, h.expiry)
    assert chain.expiry == h.expiry
    assert chain.source == h.name
    assert chain.rows
    strikes = [r.strike for r in chain.rows]
    assert strikes == sorted(strikes)
    assert len(set(strikes)) == len(strikes)
    assert any(r.call and r.call.last_price is not None for r in chain.rows)
    assert any(r.put and r.put.last_price is not None for r in chain.rows)


def test_expired_session_maps_to_session_expired(h: Harness) -> None:
    provider, _ = h.build_expired()
    with pytest.raises(SessionExpired):
        provider.quotes(session_for(h.name), [h.equity])


def test_credentials_never_appear_in_errors(h: Harness) -> None:
    provider, _ = h.build_expired()
    with pytest.raises(ProviderError) as exc:
        provider.quotes(session_for(h.name), [h.equity])
    chain: BaseException | None = exc.value
    while chain is not None:
        assert LEAK_CANARY not in str(chain)
        assert LEAK_CANARY not in repr(chain.args)
        chain = chain.__cause__ or chain.__context__


def test_requests_send_credentials_only_in_headers(h: Harness) -> None:
    provider, router = h.build()
    provider.quotes(session_for(h.name), [h.equity])
    for req in router.requests:
        assert LEAK_CANARY not in str(req.url)
