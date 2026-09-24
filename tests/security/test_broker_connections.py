"""Broker connections: encrypted storage, login flows, CSRF state, masking, the HTTP API."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from marketdata.india import (
    IST,
    InvalidCredentials,
    ProviderSession,
    ProviderUnavailable,
    Secret,
)
from marketdata.india.angel import AngelProvider
from marketdata.india.kite import KiteProvider
from marketdata.india.upstox import UpstoxProvider
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market import brokers as brokers_mod
from src.modules.market.brokers import BrokerError, BrokerManager, _StateStore
from src.platform.persistence.migrations import _m128_broker_connections
from src.platform.persistence.models import BrokerConnection
from src.platform.security.credential_vault import (
    CredentialVault,
    VaultNotConfigured,
    generate_key_spec,
)

SECRET = "SECRET_api_secret_value_42"
TOKEN = "ACCESS_token_value_99"
NOW = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)  # 08:30 IST
VAULT_SPEC = generate_key_spec("k1")


# --- fakes ---------------------------------------------------------------------------


def _session(provider: str, credential_id: str, user_id: str, now: datetime) -> ProviderSession:
    return ProviderSession(
        provider=provider,
        credential_id=credential_id,
        user_id=user_id,
        access_token=Secret(TOKEN),
        expires_at=now + timedelta(hours=20),
    )


class FakeKite(KiteProvider):
    def __init__(self) -> None:
        self.fail: Exception | None = None
        self.seen: dict[str, Any] = {}

    def exchange_request_token(self, **kw: Any) -> ProviderSession:  # type: ignore[override]
        self.seen = kw
        if self.fail:
            raise self.fail
        return _session("kite", kw["credential_id"], kw["user_id"], kw["now"])


class FakeUpstox(UpstoxProvider):
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def exchange_code(self, **kw: Any) -> ProviderSession:  # type: ignore[override]
        self.seen = kw
        return _session("upstox", kw["credential_id"], kw["user_id"], kw["now"])


class FakeAngel(AngelProvider):
    def __init__(self) -> None:
        self.fail: Exception | None = None
        self.seen: dict[str, Any] = {}

    def login(self, **kw: Any) -> ProviderSession:  # type: ignore[override]
        self.seen = kw
        if self.fail:
            raise self.fail
        return _session("angel", kw["credential_id"], kw["user_id"], kw["now"])


# --- fixtures ------------------------------------------------------------------------


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    BrokerConnection.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def manager(clock: Clock) -> BrokerManager:
    return BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(VAULT_SPEC),
        providers={"kite": FakeKite(), "upstox": FakeUpstox(), "angel": FakeAngel()},
        env={},
        clock=clock,
    )


def save_kite(manager: BrokerManager, db: Session, **kw: Any) -> dict[str, Any]:
    return manager.save(db, "kite", {"api_key": "kite_key_123", "api_secret": SECRET}, **kw)


def connect_kite(manager: BrokerManager, db: Session) -> dict[str, Any]:
    save_kite(manager, db)
    state = parse_qs(urlparse(manager.start_login(db, "kite")).query)["redirect_params"][0]
    return manager.complete_redirect(db, "kite", state=state.removeprefix("state="), token="rt")


# --- storage and masking ---------------------------------------------------------------


def test_save_encrypts_and_masks(manager: BrokerManager, db: Session) -> None:
    public = save_kite(manager, db)
    assert public["status"] == "disconnected"
    assert public["key_hint"].startswith("kit")
    assert "•" in public["key_hint"]
    row = db.query(BrokerConnection).one()
    assert SECRET not in row.credentials_enc
    assert SECRET not in json.dumps(public)
    assert SECRET not in json.dumps(manager.connections(db))


def test_save_requires_every_field(manager: BrokerManager, db: Session) -> None:
    with pytest.raises(BrokerError, match="api_secret is required"):
        manager.save(db, "kite", {"api_key": "k"})
    with pytest.raises(BrokerError, match="Unknown broker"):
        manager.save(db, "nope", {})


def test_masked_values_keep_the_stored_secret(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    # The edit form sends the masked hint back; nothing changes and the session survives.
    public = manager.save(db, "kite", {"api_key": "kit••••••_123", "api_secret": ""})
    assert public["status"] == "connected"
    (session,) = manager.sessions(db)
    assert session.api_key.reveal() == "kite_key_123"


def test_new_keys_drop_the_old_session(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    public = manager.save(db, "kite", {"api_key": "other_key_456", "api_secret": SECRET})
    assert public["status"] == "disconnected"
    assert manager.sessions(db) == []


def test_ciphertext_cannot_move_between_rows(manager: BrokerManager, db: Session) -> None:
    save_kite(manager, db)
    kite_row = db.query(BrokerConnection).one()
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1234"})
    angel_row = db.query(BrokerConnection).filter_by(provider="angel").one()
    angel_row.credentials_enc = kite_row.credentials_enc  # a row swap / tampering attempt
    db.commit()
    with pytest.raises(BrokerError, match="could not be read"):
        manager.login_with_totp(db, "angel", "123456")


def test_vault_missing_disables_connections(db: Session) -> None:
    def no_vault() -> CredentialVault:
        raise VaultNotConfigured("not set")

    m = BrokerManager(vault_factory=no_vault, providers={"kite": FakeKite()}, env={})
    assert m.vault_configured() is False
    with pytest.raises(BrokerError, match="CREDENTIALS_MASTER_KEY"):
        save_kite(m, db)


# --- Kite / Upstox redirect logins -----------------------------------------------------


def test_kite_login_round_trip(manager: BrokerManager, db: Session) -> None:
    public = connect_kite(manager, db)
    assert public["status"] == "connected"
    assert public["session_expires_at"] == (NOW + timedelta(hours=20)).astimezone(IST).isoformat()
    kite = manager.providers["kite"]
    assert isinstance(kite, FakeKite)
    assert kite.seen["api_secret"].reveal() == SECRET
    assert kite.seen["request_token"] == "rt"
    row = db.query(BrokerConnection).one()
    assert TOKEN not in (row.session_enc or "")
    (session,) = manager.sessions(db)
    assert session.access_token.reveal() == TOKEN
    assert session.credential_id == row.id


def test_login_state_is_single_use_and_bound_to_provider(
    manager: BrokerManager, db: Session
) -> None:
    save_kite(manager, db)
    url = manager.start_login(db, "kite")
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    with pytest.raises(BrokerError, match="expired or was already used"):
        manager.complete_redirect(db, "upstox", state=state, token="x")  # wrong provider
    save_kite(manager, db)
    state2 = parse_qs(
        parse_qs(urlparse(manager.start_login(db, "kite")).query)["redirect_params"][0]
    )["state"][0]
    manager.complete_redirect(db, "kite", state=state2, token="rt")
    with pytest.raises(BrokerError, match="expired or was already used"):
        manager.complete_redirect(db, "kite", state=state2, token="rt")  # replay
    with pytest.raises(BrokerError):
        manager.complete_redirect(db, "kite", state="forged", token="rt")


def test_login_state_expires() -> None:
    t = [0.0]
    store = _StateStore(clock=lambda: t[0])
    state = store.issue("local", "kite")
    t[0] = 601.0
    with pytest.raises(BrokerError):
        store.consume(state, "kite")
    store.issue("local", "kite")  # issuing prunes expired states
    assert state not in store._states


def test_upstox_login(manager: BrokerManager, db: Session) -> None:
    manager.save(
        db,
        "upstox",
        {"client_id": "cid", "client_secret": SECRET, "redirect_uri": "https://x/cb"},
    )
    url = manager.start_login(db, "upstox")
    q = parse_qs(urlparse(url).query)
    assert q["redirect_uri"] == ["https://x/cb"]
    public = manager.complete_redirect(db, "upstox", state=q["state"][0], token="the-code")
    assert public["status"] == "connected"
    upstox = manager.providers["upstox"]
    assert isinstance(upstox, FakeUpstox)
    assert upstox.seen["code"] == "the-code"
    assert manager.sessions(db)[0].api_key.reveal() == "cid"


def test_rejected_login_is_recorded_safely(manager: BrokerManager, db: Session) -> None:
    kite = manager.providers["kite"]
    assert isinstance(kite, FakeKite)
    kite.fail = InvalidCredentials("kite", "login rejected")
    public = connect_kite(manager, db)
    assert public["status"] == "error"
    assert public["last_error"] == "Login was rejected. Check your details and try again."
    kite.fail = ProviderUnavailable("kite", "HTTP 503")
    public = connect_kite(manager, db)
    assert public["last_error"] == "kite: HTTP 503"


def test_redirect_login_needs_saved_keys(manager: BrokerManager, db: Session) -> None:
    with pytest.raises(BrokerError, match="Save your API details"):
        manager.start_login(db, "kite")
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1234"})
    with pytest.raises(BrokerError, match="does not use a login redirect"):
        manager.start_login(db, "angel")


# --- Angel TOTP ------------------------------------------------------------------------


def test_angel_totp_login_keeps_no_totp(manager: BrokerManager, db: Session) -> None:
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1234"})
    public = manager.login_with_totp(db, "angel", " 654321 ")
    assert public["status"] == "connected"
    angel = manager.providers["angel"]
    assert isinstance(angel, FakeAngel)
    assert angel.seen["totp"] == "654321"
    row = db.query(BrokerConnection).one()
    vault = CredentialVault.from_spec(VAULT_SPEC)
    stored = vault.decrypt(row.credentials_enc, context=f"local|{row.id}|credentials")
    assert "654321" not in json.dumps(stored)
    assert set(stored) == {"api_key", "client_code", "pin"}
    (session,) = manager.sessions(db)
    assert session.extra["client_code"].reveal() == "C1"


@pytest.mark.parametrize("totp", ["", "12345", "abcdef", "1234567"])
def test_angel_totp_format(manager: BrokerManager, db: Session, totp: str) -> None:
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1234"})
    with pytest.raises(BrokerError, match="6-digit"):
        manager.login_with_totp(db, "angel", totp)


def test_totp_only_for_angel(manager: BrokerManager, db: Session) -> None:
    with pytest.raises(BrokerError, match="does not use TOTP"):
        manager.login_with_totp(db, "kite", "123456")


def test_angel_rejected(manager: BrokerManager, db: Session) -> None:
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1234"})
    angel = manager.providers["angel"]
    assert isinstance(angel, FakeAngel)
    angel.fail = InvalidCredentials("angel", "bad totp")
    assert manager.login_with_totp(db, "angel", "123456")["status"] == "error"


# --- status, sessions, lifecycle -------------------------------------------------------


def test_status_turns_expired_with_time(manager: BrokerManager, db: Session, clock: Clock) -> None:
    connect_kite(manager, db)
    clock.now = NOW + timedelta(hours=21)
    (public,) = manager.connections(db)
    assert public["status"] == "expired"
    (session,) = manager.sessions(db)  # still handed out, so the service reports it
    assert session.is_expired(clock.now)


def test_mark_needs_reconnect(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    manager.mark_needs_reconnect(db, ["kite", "upstox"])
    assert manager.connections(db)[0]["status"] == "expired"


def test_sessions_follow_priority_and_skip_disabled(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    manager.save(db, "kite", {"api_key": "kite_key_123", "api_secret": SECRET}, priority=5)
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1"}, priority=1)
    manager.login_with_totp(db, "angel", "123456")
    assert [s.provider for s in manager.sessions(db)] == ["angel", "kite"]
    manager.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1"}, enabled=False)
    assert [s.provider for s in manager.sessions(db)] == ["kite"]


def test_unreadable_rows_are_flagged_not_fatal(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    row = db.query(BrokerConnection).one()
    row.session_enc = "v1.k1.garbage.garbage"
    db.commit()
    assert manager.sessions(db) == []
    assert manager.connections(db)[0]["status"] == "error"


def test_disconnect_and_delete(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    assert manager.disconnect(db, "kite")["status"] == "disconnected"
    assert manager.sessions(db) == []
    manager.delete(db, "kite")
    assert manager.connections(db) == []
    with pytest.raises(BrokerError, match="No such connection"):
        manager.delete(db, "kite")


def test_users_are_isolated(manager: BrokerManager, db: Session) -> None:
    connect_kite(manager, db)
    assert manager.connections(db, user_id="someone-else") == []
    assert manager.sessions(db, user_id="someone-else") == []


def test_available_and_yfinance_gate(db: Session) -> None:
    locked = BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(VAULT_SPEC),
        providers={"kite": FakeKite()},
        env={},
    )
    assert "yfinance" not in [a["provider"] for a in locked.available()]
    with pytest.raises(BrokerError, match="Unofficial data is disabled"):
        locked.save(db, "yfinance", {})

    dev = BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(VAULT_SPEC),
        env={"ALLOW_UNOFFICIAL_DATA": "true", "APP_ENV": "development"},
    )
    available = {a["provider"]: a for a in dev.available()}
    assert available["yfinance"]["quality"] == "unofficial_delayed"
    assert available["kite"]["fields"] == ["api_key", "api_secret"]
    assert dev.save(db, "yfinance", {})["status"] == "connected"
    (session,) = dev.sessions(db)
    assert session.provider == "yfinance"


def test_default_providers_without_unofficial_data() -> None:
    assert set(brokers_mod.default_providers({})) == {"kite", "upstox", "angel"}


def test_provider_not_available(db: Session) -> None:
    m = BrokerManager(
        vault_factory=lambda: CredentialVault.from_spec(VAULT_SPEC), providers={}, env={}
    )
    m.save(db, "angel", {"api_key": "a", "client_code": "C1", "pin": "1"})
    with pytest.raises(BrokerError, match="not available"):
        m.login_with_totp(db, "angel", "123456")


def test_nothing_secret_is_logged(
    manager: BrokerManager, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    connect_kite(manager, db)
    manager.sessions(db)
    assert SECRET not in caplog.text
    assert TOKEN not in caplog.text


def test_get_broker_manager_is_a_singleton() -> None:
    brokers_mod._manager = None
    assert brokers_mod.get_broker_manager() is brokers_mod.get_broker_manager()
    brokers_mod._manager = None


# --- migration -------------------------------------------------------------------------


def test_migration_creates_table_idempotently() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _m128_broker_connections(conn)
        _m128_broker_connections(conn)
        conn.execute(text("INSERT INTO broker_connections(id, provider) VALUES ('a', 'kite')"))
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(text("INSERT INTO broker_connections(id, provider) VALUES ('b', 'kite')"))
    columns = {c["name"] for c in inspect(engine).get_columns("broker_connections")}
    model_columns = set(BrokerConnection.__table__.columns.keys())
    assert columns == model_columns


# --- HTTP API --------------------------------------------------------------------------


@pytest.fixture
def client(manager: BrokerManager, db: Session) -> Iterator[TestClient]:
    from src.bootstrap.application import app
    from src.modules.administration.api.auth import get_current_user
    from src.modules.market.brokers import get_broker_manager
    from src.platform.persistence.database import get_db

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_broker_manager] = lambda: manager
    app.dependency_overrides[get_current_user] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _data(resp: Any) -> Any:
    body = resp.json()
    return body.get("data", body) if isinstance(body, dict) else body


def test_api_flow(client: TestClient) -> None:
    body = _data(client.get("/api/brokers"))
    assert body["vault_configured"] is True
    assert body["connections"] == []

    saved = client.put(
        "/api/brokers/kite", json={"credentials": {"api_key": "kite_key_123", "api_secret": SECRET}}
    )
    assert saved.status_code == 200
    assert SECRET not in saved.text

    login_url = _data(client.post("/api/brokers/kite/login"))["login_url"]
    state = parse_qs(parse_qs(urlparse(login_url).query)["redirect_params"][0])["state"][0]
    cb = client.get(
        f"/api/brokers/kite/callback?request_token=rt&state={state}", follow_redirects=False
    )
    assert cb.status_code == 303
    assert cb.headers["location"] == "/datasources?broker=kite&status=connected"

    listing = client.get("/api/brokers")
    assert SECRET not in listing.text
    assert TOKEN not in listing.text
    assert _data(listing)["connections"][0]["status"] == "connected"

    assert _data(client.post("/api/brokers/kite/disconnect"))["status"] == "disconnected"
    assert _data(client.delete("/api/brokers/kite"))["deleted"] is True


def test_api_errors(client: TestClient) -> None:
    assert client.put("/api/brokers/kite", json={"credentials": {}}).status_code == 400
    assert client.post("/api/brokers/kite/login").status_code == 400
    assert client.post("/api/brokers/kite/disconnect").status_code == 400
    assert client.delete("/api/brokers/kite").status_code == 400
    assert client.post("/api/brokers/angel/totp", json={"totp": "1"}).status_code == 400
    forged = client.get("/api/brokers/upstox/callback?code=x&state=forged", follow_redirects=False)
    assert forged.headers["location"] == "/datasources?broker=upstox&status=error"


def test_api_angel_totp(client: TestClient) -> None:
    client.put(
        "/api/brokers/angel",
        json={"credentials": {"api_key": "a", "client_code": "C1", "pin": "1234"}},
    )
    resp = client.post("/api/brokers/angel/totp", json={"totp": "123456"})
    assert resp.status_code == 200
    assert _data(resp)["status"] == "connected"


def test_callbacks_are_public_but_management_is_not(
    client: TestClient, manager: BrokerManager, db: Session
) -> None:
    from fastapi import HTTPException

    from src.bootstrap.application import app
    from src.modules.administration.api.auth import get_current_user

    def reject() -> None:
        raise HTTPException(status_code=401, detail="not logged in")

    app.dependency_overrides[get_current_user] = reject
    assert client.get("/api/brokers").status_code == 401
    assert client.put("/api/brokers/kite", json={"credentials": {}}).status_code == 401
    assert client.post("/api/brokers/kite/login").status_code == 401
    assert client.post("/api/brokers/angel/totp", json={"totp": "123456"}).status_code == 401
    # The browser arrives from the broker without our bearer token; state protects it.
    cb = client.get("/api/brokers/kite/callback?request_token=x&state=y", follow_redirects=False)
    assert cb.status_code == 303
