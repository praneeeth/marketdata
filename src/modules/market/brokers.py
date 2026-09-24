"""Broker connections: store credentials encrypted, run each broker's login, hand out sessions.

Credentials are encrypted with :class:`CredentialVault` and never returned by the API;
the UI only sees a masked ``key_hint``. Login flows:

- **Kite**: redirect to Kite, which calls back with ``request_token``; we exchange it.
- **Upstox**: OAuth2 authorisation code; we exchange the ``code``.
- **Angel One**: the user types a TOTP each time; we log in with it and discard it.
  No TOTP seed is ever stored (decision Q10).

Login state tokens are single-use, expire after ten minutes and are bound to the
connection, which protects the public callback routes against CSRF.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from marketdata.india import (
    IST,
    InvalidCredentials,
    MarketDataProvider,
    ProviderError,
    ProviderSession,
    Secret,
    UnofficialDataDisabled,
)
from marketdata.india.angel import AngelProvider
from marketdata.india.kite import KiteProvider
from marketdata.india.kite import login_url as kite_login_url
from marketdata.india.service import IndiaMarketData
from marketdata.india.upstox import UpstoxProvider
from marketdata.india.upstox import authorize_url as upstox_authorize_url
from marketdata.india.yfinance_dev import YFinanceProvider, unofficial_data_allowed
from sqlalchemy.orm import Session

from src.platform.persistence.models import BrokerConnection
from src.platform.security.credential_vault import (
    CredentialVault,
    VaultDecryptError,
    VaultError,
)
from src.platform.security.secrets import is_masked, mask_secret

LOCAL_USER = "local"
_STATE_TTL_S = 600.0
_MAX_ERROR = 300


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    credential_fields: tuple[str, ...]
    hint_field: str | None
    login: str  # "redirect" | "totp" | "none"
    notes: str


PROVIDERS: Mapping[str, ProviderSpec] = {
    "kite": ProviderSpec(
        "kite",
        "Zerodha Kite Connect",
        ("api_key", "api_secret"),
        "api_key",
        "redirect",
        "Needs a Kite Connect app with the market data add-on (paid). Set the app's "
        "redirect URL to <this server>/api/brokers/kite/callback. Log in again each day.",
    ),
    "upstox": ProviderSpec(
        "upstox",
        "Upstox",
        ("client_id", "client_secret", "redirect_uri"),
        "client_id",
        "redirect",
        "Set the Upstox app's redirect URL to <this server>/api/brokers/upstox/callback "
        "and enter the same URL here. Log in again each day.",
    ),
    "angel": ProviderSpec(
        "angel",
        "Angel One SmartAPI",
        ("api_key", "client_code", "pin"),
        "client_code",
        "totp",
        "Log in with the 6-digit code from your authenticator app. The code is used once "
        "and never stored.",
    ),
    "yfinance": ProviderSpec(
        "yfinance",
        "Yahoo Finance (development only, unofficial and delayed)",
        (),
        None,
        "none",
        "Only available when ALLOW_UNOFFICIAL_DATA=true and APP_ENV is a development "
        "environment. Never enable in production.",
    ),
}


class BrokerError(Exception):
    """A user-facing problem with a broker connection. Messages are safe to show."""


class _StateStore:
    """Single-use login state tokens: ``state -> (user_id, provider, expires)``."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, tuple[str, str, float]] = {}

    def issue(self, user_id: str, provider: str) -> str:
        state = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._states = {k: v for k, v in self._states.items() if v[2] > now}
            self._states[state] = (user_id, provider, now + _STATE_TTL_S)
        return state

    def consume(self, state: str, provider: str) -> str:
        with self._lock:
            entry = self._states.pop(state, None)
        if entry is None or entry[1] != provider or entry[2] <= self._clock():
            raise BrokerError("This login link has expired or was already used. Start again.")
        return entry[0]


def default_providers(env: Mapping[str, str]) -> dict[str, MarketDataProvider]:
    providers: dict[str, MarketDataProvider] = {
        "kite": KiteProvider(),
        "upstox": UpstoxProvider(),
        "angel": AngelProvider(),
    }
    with contextlib.suppress(UnofficialDataDisabled):
        providers["yfinance"] = YFinanceProvider(env)
    return providers


class BrokerManager:
    def __init__(
        self,
        *,
        vault_factory: Callable[[], CredentialVault] = CredentialVault.from_env,
        providers: Mapping[str, MarketDataProvider] | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        state_store: _StateStore | None = None,
    ) -> None:
        self._env = dict(os.environ if env is None else env)
        self._vault_factory = vault_factory
        self.providers = dict(default_providers(self._env) if providers is None else providers)
        self.data = IndiaMarketData(self.providers)
        self._clock = clock
        self._states = state_store or _StateStore()

    # --- queries ---------------------------------------------------------------------

    def vault_configured(self) -> bool:
        try:
            self._vault_factory()
        except VaultError:
            return False
        return True

    def available(self) -> list[dict[str, Any]]:
        out = []
        for spec in PROVIDERS.values():
            if spec.name == "yfinance" and not unofficial_data_allowed(self._env):
                continue
            out.append(
                {
                    "provider": spec.name,
                    "label": spec.label,
                    "fields": list(spec.credential_fields),
                    "login": spec.login,
                    "notes": spec.notes,
                    "quality": self.providers[spec.name].quality.value
                    if spec.name in self.providers
                    else None,
                }
            )
        return out

    def connections(self, db: Session, user_id: str = LOCAL_USER) -> list[dict[str, Any]]:
        rows = (
            db.query(BrokerConnection)
            .filter(BrokerConnection.user_id == user_id)
            .order_by(BrokerConnection.priority, BrokerConnection.provider)
            .all()
        )
        return [self._public(row) for row in rows]

    def sessions(self, db: Session, user_id: str = LOCAL_USER) -> list[ProviderSession]:
        """The user's usable sessions in priority order, for :class:`IndiaMarketData`.

        Expired sessions are included (the data service skips and reports them) so the UI
        can prompt "reconnect". Rows that fail to decrypt are marked and skipped.
        """
        rows = (
            db.query(BrokerConnection)
            .filter(BrokerConnection.user_id == user_id, BrokerConnection.enabled.is_(True))
            .order_by(BrokerConnection.priority, BrokerConnection.provider)
            .all()
        )
        out: list[ProviderSession] = []
        vault: CredentialVault | None = None
        for row in rows:
            if row.provider == "yfinance":
                if "yfinance" in self.providers:
                    out.append(ProviderSession("yfinance", row.id, row.user_id))
                continue
            if not row.session_enc:
                continue
            try:
                vault = vault or self._vault_factory()
                creds = vault.decrypt(row.credentials_enc, context=_ctx(row, "credentials"))
                token = vault.decrypt(row.session_enc, context=_ctx(row, "session"))
            except VaultError:
                row.status, row.last_error = "error", "Stored credentials could not be read."
                db.commit()
                continue
            out.append(self._session(row, creds, token.get("access_token", "")))
        return out

    # --- commands --------------------------------------------------------------------

    def save(
        self,
        db: Session,
        provider: str,
        credentials: Mapping[str, str],
        *,
        priority: int = 0,
        enabled: bool = True,
        user_id: str = LOCAL_USER,
    ) -> dict[str, Any]:
        spec = self._spec(provider)
        if provider == "yfinance" and "yfinance" not in self.providers:
            raise BrokerError("Unofficial data is disabled on this server.")
        row = self._row(db, user_id, provider)
        vault = self._vault() if spec.credential_fields else None
        stored: dict[str, str] = {}
        if row is not None and row.credentials_enc and vault is not None:
            stored = self._decrypt_or_empty(vault, row, "credentials")
        merged: dict[str, str] = {}
        for field in spec.credential_fields:
            incoming = str(credentials.get(field, "") or "").strip()
            value = stored.get(field, "") if not incoming or is_masked(incoming) else incoming
            if not value:
                raise BrokerError(f"{field} is required for {spec.label}.")
            merged[field] = value
        if row is None:
            row = BrokerConnection(id=uuid.uuid4().hex, user_id=user_id, provider=provider)
            db.add(row)
        changed = merged != stored
        row.priority, row.enabled = priority, enabled
        if vault is not None:
            row.credentials_enc = vault.encrypt(merged, context=_ctx(row, "credentials"))
        row.key_hint = mask_secret(merged[spec.hint_field]) if spec.hint_field else ""
        if provider == "yfinance":
            row.status = "connected"
        elif changed:
            # New keys invalidate the old session.
            row.session_enc, row.session_expires_at, row.status = None, None, "disconnected"
        db.commit()
        return self._public(row)

    def delete(self, db: Session, provider: str, user_id: str = LOCAL_USER) -> None:
        row = self._row(db, user_id, provider)
        if row is None:
            raise BrokerError("No such connection.")
        self.data.forget_credential(row.id)
        db.delete(row)
        db.commit()

    def disconnect(self, db: Session, provider: str, user_id: str = LOCAL_USER) -> dict[str, Any]:
        row = self._require(db, user_id, provider)
        row.session_enc, row.session_expires_at = None, None
        row.status, row.last_error = "disconnected", ""
        db.commit()
        return self._public(row)

    def start_login(self, db: Session, provider: str, user_id: str = LOCAL_USER) -> str:
        """Return the URL to send the user to (Kite and Upstox only)."""
        row = self._require(db, user_id, provider)
        creds = self._decrypt_or_raise(self._vault(), row, "credentials")
        state = self._states.issue(user_id, provider)
        if provider == "kite":
            return kite_login_url(Secret(creds["api_key"]), state=state)
        if provider == "upstox":
            return upstox_authorize_url(Secret(creds["client_id"]), creds["redirect_uri"], state)
        raise BrokerError(f"{self._spec(provider).label} does not use a login redirect.")

    def complete_redirect(
        self, db: Session, provider: str, *, state: str, token: str
    ) -> dict[str, Any]:
        """Finish a Kite (``request_token``) or Upstox (``code``) login."""
        user_id = self._states.consume(state, provider)
        row = self._require(db, user_id, provider)
        vault = self._vault()
        creds = self._decrypt_or_raise(vault, row, "credentials")
        now = self._clock()
        try:
            if provider == "kite":
                kite = self._provider(provider, KiteProvider)
                session = kite.exchange_request_token(
                    credential_id=row.id,
                    user_id=user_id,
                    api_key=Secret(creds["api_key"]),
                    api_secret=Secret(creds["api_secret"]),
                    request_token=token,
                    now=now,
                )
            else:
                upstox = self._provider(provider, UpstoxProvider)
                session = upstox.exchange_code(
                    credential_id=row.id,
                    user_id=user_id,
                    client_id=Secret(creds["client_id"]),
                    client_secret=Secret(creds["client_secret"]),
                    redirect_uri=creds["redirect_uri"],
                    code=token,
                    now=now,
                )
        except ProviderError as e:
            return self._login_failed(db, row, e)
        return self._store_session(db, vault, row, session)

    def login_with_totp(
        self, db: Session, provider: str, totp: str, user_id: str = LOCAL_USER
    ) -> dict[str, Any]:
        if provider != "angel":
            raise BrokerError(f"{self._spec(provider).label} does not use TOTP login.")
        code = totp.strip()
        if not (code.isdigit() and len(code) == 6):
            raise BrokerError("Enter the 6-digit code from your authenticator app.")
        row = self._require(db, user_id, provider)
        vault = self._vault()
        creds = self._decrypt_or_raise(vault, row, "credentials")
        try:
            session = self._provider(provider, AngelProvider).login(
                credential_id=row.id,
                user_id=user_id,
                api_key=Secret(creds["api_key"]),
                client_code=Secret(creds["client_code"]),
                pin=Secret(creds["pin"]),
                totp=code,
                now=self._clock(),
            )
        except ProviderError as e:
            return self._login_failed(db, row, e)
        return self._store_session(db, vault, row, session)

    def mark_needs_reconnect(
        self, db: Session, providers: Sequence[str], user_id: str = LOCAL_USER
    ) -> None:
        """Record what the data service reported (``AllProvidersFailed.needs_reconnect``)."""
        for provider in providers:
            row = self._row(db, user_id, provider)
            if row is not None and row.status == "connected":
                row.status = "expired"
        db.commit()

    # --- internals -------------------------------------------------------------------

    def _store_session(
        self, db: Session, vault: CredentialVault, row: BrokerConnection, session: ProviderSession
    ) -> dict[str, Any]:
        row.session_enc = vault.encrypt(
            {"access_token": session.access_token.reveal()}, context=_ctx(row, "session")
        )
        expires = session.expires_at
        row.session_expires_at = expires.astimezone(UTC).replace(tzinfo=None) if expires else None
        row.status, row.last_error = "connected", ""
        db.commit()
        self.data.forget_credential(row.id)
        return self._public(row)

    def _login_failed(
        self, db: Session, row: BrokerConnection, error: ProviderError
    ) -> dict[str, Any]:
        row.status = "error"
        row.last_error = (
            "Login was rejected. Check your details and try again."
            if isinstance(error, InvalidCredentials)
            else str(error)[:_MAX_ERROR]
        )
        db.commit()
        return self._public(row)

    def _session(
        self, row: BrokerConnection, creds: Mapping[str, str], access_token: str
    ) -> ProviderSession:
        expires = row.session_expires_at
        api_key = creds.get("api_key") or creds.get("client_id", "")
        extra = {"client_code": Secret(creds["client_code"])} if "client_code" in creds else {}
        return ProviderSession(
            provider=row.provider,
            credential_id=row.id,
            user_id=row.user_id,
            api_key=Secret(api_key),
            access_token=Secret(access_token),
            extra=extra,
            expires_at=expires.replace(tzinfo=UTC) if expires else None,
        )

    def _public(self, row: BrokerConnection) -> dict[str, Any]:
        expires = row.session_expires_at.replace(tzinfo=UTC) if row.session_expires_at else None
        status = row.status
        if status == "connected" and expires is not None and expires <= self._clock():
            status = "expired"
        spec = PROVIDERS.get(row.provider)
        return {
            "provider": row.provider,
            "label": spec.label if spec else row.provider,
            "status": status,
            "key_hint": row.key_hint,
            "priority": row.priority,
            "enabled": bool(row.enabled),
            "session_expires_at": expires.astimezone(IST).isoformat() if expires else None,
            "last_error": row.last_error,
            "quality": self.providers[row.provider].quality.value
            if row.provider in self.providers
            else None,
        }

    def _spec(self, provider: str) -> ProviderSpec:
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise BrokerError(f"Unknown broker {provider!r}.")
        return spec

    def _vault(self) -> CredentialVault:
        try:
            return self._vault_factory()
        except VaultError:
            raise BrokerError(
                "Broker connections are disabled: CREDENTIALS_MASTER_KEY is not configured."
            ) from None

    def _provider(self, provider: str, kind: type[Any]) -> Any:
        instance = self.providers.get(provider)
        if not isinstance(instance, kind):
            raise BrokerError(f"{self._spec(provider).label} is not available.")
        return instance

    @staticmethod
    def _row(db: Session, user_id: str, provider: str) -> BrokerConnection | None:
        return (
            db.query(BrokerConnection)
            .filter(BrokerConnection.user_id == user_id, BrokerConnection.provider == provider)
            .one_or_none()
        )

    def _require(self, db: Session, user_id: str, provider: str) -> BrokerConnection:
        self._spec(provider)
        row = self._row(db, user_id, provider)
        if row is None:
            raise BrokerError("Save your API details for this broker first.")
        return row

    @staticmethod
    def _decrypt_or_empty(
        vault: CredentialVault, row: BrokerConnection, field: str
    ) -> dict[str, str]:
        try:
            return vault.decrypt(row.credentials_enc, context=_ctx(row, field))
        except VaultDecryptError:
            return {}

    @staticmethod
    def _decrypt_or_raise(
        vault: CredentialVault, row: BrokerConnection, field: str
    ) -> dict[str, str]:
        try:
            return vault.decrypt(row.credentials_enc, context=_ctx(row, field))
        except VaultDecryptError:
            raise BrokerError("Stored credentials could not be read; enter them again.") from None


def _ctx(row: BrokerConnection, field: str) -> str:
    return f"{row.user_id}|{row.id}|{field}"


_manager: BrokerManager | None = None
_manager_lock = threading.Lock()


def get_broker_manager() -> BrokerManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BrokerManager()
        return _manager
