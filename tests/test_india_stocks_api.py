"""Indian stocks in the watchlist: search via the user's broker, canonical symbols, provenance."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from marketdata.india import (
    Exchange,
    Instrument,
    InstrumentType,
    ProviderSession,
    ProviderUnavailable,
    Secret,
)
from marketdata.india.kite import KiteProvider
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market.brokers import BrokerManager
from src.platform.persistence.database import Base
from src.platform.security.credential_vault import CredentialVault, generate_key_spec

NOW = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
SPEC = generate_key_spec("k1")

MASTER = {
    Exchange.NSE: [
        ("INFY", "INFOSYS", InstrumentType.EQUITY),
        ("INFIBEAM", "INFIBEAM AVENUES", InstrumentType.EQUITY),
        ("TCS", "TATA CONSULTANCY", InstrumentType.EQUITY),
        ("NIFTY 50", "NIFTY 50", InstrumentType.INDEX),
        ("NIFTY26OCTFUT", "NIFTY", InstrumentType.FUTURE),
    ],
    Exchange.BSE: [("INFY", "INFOSYS", InstrumentType.EQUITY)],
}


class FakeKite(KiteProvider):
    def __init__(self) -> None:
        self.master_calls = 0
        self.fail = False

    def exchange_request_token(self, **kw: Any) -> ProviderSession:  # type: ignore[override]
        return ProviderSession(
            provider="kite",
            credential_id=kw["credential_id"],
            user_id=kw["user_id"],
            access_token=Secret("tok"),
            expires_at=kw["now"] + timedelta(hours=20),
        )

    def instruments(self, session: ProviderSession, exchange: Exchange):  # type: ignore[no-untyped-def,override]
        self.master_calls += 1
        if self.fail:
            raise ProviderUnavailable("kite", "down")
        for sym, name, itype in MASTER.get(exchange, []):
            yield Instrument(
                exchange,
                sym,
                name,
                itype,
                "X",
                1,
                Decimal("0.05"),
                provider_ids=(("kite", f"{exchange}-{sym}"),),
            )


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def kite() -> FakeKite:
    return FakeKite()


@pytest.fixture
def manager(kite: FakeKite) -> BrokerManager:
    return BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(SPEC),
        providers={"kite": kite},
        env={},
        clock=lambda: NOW,
    )


def connect(manager: BrokerManager, db: Session) -> None:
    from urllib.parse import parse_qs, urlparse

    manager.save(db, "kite", {"api_key": "key", "api_secret": "secret"})
    url = manager.start_login(db, "kite")
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    manager.complete_redirect(db, "kite", state=state, token="rt")


def test_search_ranks_exact_then_prefix_then_name(manager: BrokerManager, db: Session) -> None:
    connect(manager, db)
    results = manager.search_instruments(db, "infy")
    assert [r["symbol"] for r in results] == ["INFY", "BSE:INFY"]
    results = manager.search_instruments(db, "INF")
    assert [r["symbol"] for r in results][:2] == ["INFIBEAM", "INFY"]
    assert manager.search_instruments(db, "consultancy")[0]["symbol"] == "TCS"
    assert manager.search_instruments(db, "nifty 50")[0]["symbol"] == "NIFTY 50"
    assert all(r["symbol"] != "NIFTY26OCTFUT" for r in manager.search_instruments(db, "NIFTY"))
    assert manager.search_instruments(db, "   ") == []
    assert manager.search_instruments(db, "IN", limit=1) == [
        {"symbol": "INFIBEAM", "name": "INFIBEAM AVENUES", "market": "IN", "exchange": "NSE"}
    ]


def test_search_uses_the_cached_master(manager: BrokerManager, db: Session, kite: FakeKite) -> None:
    connect(manager, db)
    manager.search_instruments(db, "INFY")
    manager.search_instruments(db, "TCS")
    assert kite.master_calls == 2  # NSE + BSE once each


def test_search_without_a_usable_broker(
    manager: BrokerManager, db: Session, kite: FakeKite
) -> None:
    assert manager.search_instruments(db, "INFY") == []  # nothing connected
    connect(manager, db)
    kite.fail = True
    assert manager.search_instruments(db, "INFY") == []


def test_search_skips_expired_sessions(kite: FakeKite, db: Session) -> None:
    clock = {"now": NOW}
    m = BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(SPEC),
        providers={"kite": kite},
        env={},
        clock=lambda: clock["now"],
    )
    connect(m, db)
    clock["now"] = NOW + timedelta(days=2)
    assert m.search_instruments(db, "INFY") == []


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def client(manager: BrokerManager, db: Session) -> Iterator[TestClient]:
    from src.bootstrap.application import app
    from src.modules.administration.api.auth import get_current_user
    from src.modules.market.api import stocks as stocks_api
    from src.platform.persistence.database import get_db

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: None
    original = stocks_api.get_broker_manager
    stocks_api.get_broker_manager = lambda: manager  # type: ignore[assignment]
    try:
        yield TestClient(app)
    finally:
        stocks_api.get_broker_manager = original  # type: ignore[assignment]
        app.dependency_overrides.clear()


def _data(resp: Any) -> Any:
    body = resp.json()
    return body.get("data", body) if isinstance(body, dict) and "data" in body else body


def test_api_search_india(client: TestClient, manager: BrokerManager, db: Session) -> None:
    connect(manager, db)
    results = _data(client.get("/api/stocks/search", params={"q": "infy", "market": "IN"}))
    assert results[0] == {"symbol": "INFY", "name": "INFOSYS", "market": "IN", "exchange": "NSE"}


def test_api_search_ignores_the_market_filter(
    client: TestClient, manager: BrokerManager, db: Session
) -> None:
    """India is the only market, so every search goes to the broker instrument list."""
    connect(manager, db)
    for params in ({"q": "infy"}, {"q": "infy", "market": "US"}):
        results = _data(client.get("/api/stocks/search", params=params))
        assert [r["market"] for r in results] == ["IN", "IN"]


@pytest.mark.parametrize(
    ("given", "stored"),
    [("infy", "INFY"), ("NSE:INFY", "INFY"), ("bse:500209", "BSE:500209"), ("m&m", "M&M")],
)
def test_api_create_india_stock_normalises(client: TestClient, given: str, stored: str) -> None:
    resp = client.post("/api/stocks", json={"symbol": given, "name": "", "market": "IN"})
    assert resp.status_code == 200
    body = _data(resp)
    assert body["symbol"] == stored
    assert body["market"] == "IN"
    assert body["name"]
    dup = client.post("/api/stocks", json={"symbol": stored, "name": "x", "market": "IN"})
    assert dup.status_code == 400


@pytest.mark.parametrize("bad", ["NSE:", "INFY!", "MCX:GOLD"])
def test_api_rejects_bad_india_symbols(client: TestClient, bad: str) -> None:
    resp = client.post("/api/stocks", json={"symbol": bad, "name": "x", "market": "IN"})
    assert resp.status_code == 400


def test_api_quotes_carry_provenance(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.modules.market.api import stocks as stocks_api

    client.post("/api/stocks", json={"symbol": "INFY", "name": "Infosys", "market": "IN"})

    def fake_rows(symbols: list[str], market: str) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "INFY",
                "current_price": 1412.95,
                "change_pct": 1.68,
                "change_amount": 23.3,
                "prev_close": 1389.65,
                "source": "yfinance",
                "quality": "unofficial_delayed",
            }
        ]

    monkeypatch.setattr(stocks_api, "md_quote_rows", fake_rows)
    quotes = _data(client.get("/api/stocks/quotes"))
    assert quotes["INFY"]["quality"] == "unofficial_delayed"
    assert quotes["INFY"]["source"] == "yfinance"


def test_api_notice(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform.marketdata import india_bridge

    class B:
        def data_notice(self) -> str:
            return "Broker session expired (kite). Log in again under Data sources."

    monkeypatch.setattr(india_bridge, "get_india_bridge", lambda: B())
    from src.modules.market.api import brokers as brokers_api

    monkeypatch.setattr(brokers_api, "get_india_bridge", lambda: B())
    assert "expired" in _data(client.get("/api/brokers/notice"))["notice"]
