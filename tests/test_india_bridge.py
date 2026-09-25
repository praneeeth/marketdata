"""India bridge: market "IN" quotes and K-lines come from the user's broker sessions."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from marketdata.india import (
    IST,
    Candle,
    Capability,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentRef,
    InstrumentType,
    Interval,
    ProviderSession,
    Quote,
    Secret,
    SessionExpired,
)
from marketdata.india.service import IndiaMarketData

from src.platform.marketdata import india_bridge as bridge_mod
from src.platform.marketdata.india_bridge import IndiaBridge, parse_symbol
from src.platform.marketdata.models import MARKETS, MarketCode

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=IST)


class FakeKite:
    name = "kite"
    capabilities = frozenset(Capability)
    quality = DataQuality.OFFICIAL_REALTIME

    def __init__(self) -> None:
        self.fail: Exception | None = None
        self.candle_calls = 0

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        if self.fail:
            raise self.fail
        return [
            Quote(
                r,
                Decimal("1412.95"),
                "kite",
                self.quality,
                prev_close=Decimal("1389.65"),
                change=Decimal("23.30"),
                change_pct=Decimal("1.68"),
                volume=100,
                upper_circuit=Decimal("1528.6"),
            )
            for r in refs
        ]

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        self.candle_calls += 1
        if self.fail:
            raise self.fail
        days = [NOW - timedelta(days=d) for d in (3, 2, 1)]
        return [Candle(d, Decimal(1), Decimal(3), Decimal("0.5"), Decimal(2), 10) for d in days]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        for sym in ("INFY", "M&M", "NIFTY 50"):
            yield Instrument(
                exchange,
                sym,
                sym,
                InstrumentType.EQUITY,
                "NSE",
                1,
                Decimal("0.05"),
                provider_ids=(("kite", f"id-{sym}"),),
            )

    def corporate_actions(self, *a: Any) -> list[Any]:
        return []

    def option_chain(self, *a: Any) -> Any:
        raise NotImplementedError


class FakeDb:
    closed = 0

    def close(self) -> None:
        FakeDb.closed += 1


class FakeManager:
    def __init__(
        self, provider: FakeKite, *, connected: bool = True, expired: bool = False
    ) -> None:
        self.data = IndiaMarketData({"kite": provider}, clock=lambda: NOW)
        self.connected = connected
        self.expired = expired
        self.reconnect: list[str] = []

    def sessions(self, db: Any, user_id: str) -> list[ProviderSession]:
        if not self.connected:
            return []
        return [
            ProviderSession(
                provider="kite",
                credential_id="c1",
                user_id=user_id,
                access_token=Secret("t"),
                expires_at=NOW - timedelta(hours=1) if self.expired else NOW + timedelta(hours=18),
            )
        ]

    def mark_needs_reconnect(self, db: Any, providers: Sequence[str], user_id: str) -> None:
        self.reconnect.extend(providers)


def make(manager: FakeManager) -> IndiaBridge:
    return IndiaBridge(manager_factory=lambda: manager, db_factory=FakeDb, clock=lambda: NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("symbol", "exchange", "tradingsymbol"),
    [
        ("INFY", Exchange.NSE, "INFY"),
        (" infy ", Exchange.NSE, "INFY"),
        ("BSE:INFY", Exchange.BSE, "INFY"),
        ("nse: m&m", Exchange.NSE, "M&M"),
        ("Nifty  50", Exchange.NSE, "NIFTY 50"),
    ],
)
def test_parse_symbol(symbol: str, exchange: Exchange, tradingsymbol: str) -> None:
    assert parse_symbol(symbol) == InstrumentRef(exchange, tradingsymbol)


def test_parse_symbol_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_symbol("NSE:")


def test_in_market_definition() -> None:
    md = MARKETS[MarketCode.IN]
    pattern = re.compile(md.symbol_pattern)
    for ok in ("INFY", "NSE:INFY", "BSE:500209", "M&M", "BAJAJ-AUTO", "NIFTY 50"):
        assert pattern.match(ok), ok
    for bad in ("", "nse:infy", "MCX:GOLD", " INFY"):
        assert not pattern.match(bad), bad
    assert md.is_trading_time(datetime(2026, 9, 23, 10, 0, tzinfo=IST))  # Wednesday
    assert not md.is_trading_time(datetime(2026, 9, 23, 15, 45, tzinfo=IST))
    assert not md.is_trading_time(datetime(2026, 9, 26, 10, 0, tzinfo=IST))  # Saturday


def test_quote_rows_have_the_upstream_shape_plus_provenance() -> None:
    b = make(FakeManager(FakeKite()))
    rows = b.quote_rows(["NSE:INFY", "M&M", "  "])
    assert [r["symbol"] for r in rows] == ["NSE:INFY", "M&M"]  # echoed as asked
    row = rows[0]
    assert row["market"] == "IN"
    assert row["current_price"] == 1412.95
    assert row["change_pct"] == 1.68
    assert row["upper_circuit"] == 1528.6
    assert row["source"] == "kite"
    assert row["quality"] == "official_realtime"
    assert row["turnover"] is None
    assert b.data_notice() == ""


def test_no_broker_connected_gives_a_notice() -> None:
    b = make(FakeManager(FakeKite(), connected=False))
    assert b.quote_rows(["INFY"]) == []
    assert "No broker is connected" in b.data_notice()
    assert b.quote_rows([]) == []


def test_expired_session_marks_reconnect_and_explains() -> None:
    manager = FakeManager(FakeKite(), expired=True)
    b = make(manager)
    assert b.quote_rows(["INFY"]) == []
    assert manager.reconnect == ["kite"]
    assert "Broker session expired (kite)" in b.data_notice()


def test_revoked_token_is_reported_too() -> None:
    kite = FakeKite()
    kite.fail = SessionExpired("kite", "token revoked")
    manager = FakeManager(kite)
    b = make(manager)
    assert b.daily_bars("INFY", 10) == []
    assert manager.reconnect == ["kite"]


def test_other_failures_are_explained_without_reconnect() -> None:
    b = make(FakeManager(FakeKite()))
    assert b.quote_rows(["UNKNOWNCO"]) == []
    assert "unavailable" in b.data_notice()


def test_notice_clears_after_success() -> None:
    manager = FakeManager(FakeKite(), connected=False)
    b = make(manager)
    b.quote_rows(["INFY"])
    manager.connected = True
    b.quote_rows(["INFY"])
    assert b.data_notice() == ""


def test_daily_bars() -> None:
    b = make(FakeManager(FakeKite()))
    bars = b.daily_bars("INFY", 2)
    assert [x.date for x in bars] == ["2026-09-21", "2026-09-22"]
    assert bars[0].close == 2.0
    assert b.daily_bars("", 5) == []


def test_db_is_always_closed() -> None:
    before = FakeDb.closed
    b = make(FakeManager(FakeKite(), connected=False))
    b.quote_rows(["INFY"])
    assert FakeDb.closed == before + 1


def test_md_quote_rows_routes_in_to_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform.marketdata import marketdata_client as client

    b = make(FakeManager(FakeKite()))
    monkeypatch.setattr(bridge_mod, "get_india_bridge", lambda: b)
    rows = client.md_quote_rows(["INFY"], "IN")
    assert rows[0]["source"] == "kite"
    stock = client.md_stock_data(["INFY"], "IN")[0]
    assert stock.market is MarketCode.IN
    assert stock.current_price == 1412.95
    assert stock.turnover == 0.0


def test_klines_for_in_bypass_the_shared_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform.marketdata.collectors import kline_collector as kc

    kite = FakeKite()
    b = make(FakeManager(kite))
    monkeypatch.setattr(bridge_mod, "get_india_bridge", lambda: b)
    k = kc.KlineCollector(MarketCode.IN)
    first = k.get_klines("INFY", days=3)
    assert [x.close for x in first] == [2.0, 2.0, 2.0]
    assert not any(key.startswith("IN:") for key in kc._KLINE_CACHE)
    assert kc.get_index_klines("NIFTY 50", MarketCode.IN, days=3)
    assert kite.candle_calls == 2  # one fetch per symbol (INFY, NIFTY 50)


def test_singleton() -> None:
    bridge_mod._bridge = None
    assert bridge_mod.get_india_bridge() is bridge_mod.get_india_bridge()
    bridge_mod._bridge = None


def test_manager_is_plugged_in_by_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.bootstrap.application  # noqa: F401 - registers the broker manager
    from src.modules.market.brokers import get_broker_manager

    assert bridge_mod._manager_source is get_broker_manager
    sentinel = object()
    monkeypatch.setattr(bridge_mod, "_manager_source", lambda: sentinel)
    assert bridge_mod._manager() is sentinel
    db = bridge_mod._open_db()
    db.close()


def test_unwired_process_degrades_with_a_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge_mod, "_manager_source", None)
    b = IndiaBridge(db_factory=FakeDb, clock=lambda: NOW)  # type: ignore[arg-type]
    assert b.quote_rows(["INFY"]) == []
    assert "not available in this process" in b.data_notice()
