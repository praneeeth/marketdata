"""Broker connection API: save keys, log in, see status. Credentials are never returned.

``router`` needs a logged-in user. ``callback_router`` is public because the broker
redirects the browser there; it is protected by the single-use login ``state`` instead.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market.brokers import BrokerError, BrokerManager, get_broker_manager
from src.platform.persistence.database import get_db

router = APIRouter()
callback_router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]
Manager = Annotated[BrokerManager, Depends(get_broker_manager)]

# Where the browser lands after a broker login (the data sources page).
_RETURN_PATH = "/datasources"


class SaveRequest(BaseModel):
    credentials: dict[str, str] = Field(default_factory=dict)
    # Omitted means "keep the stored value" (new connections: priority 0, enabled).
    priority: int | None = None
    enabled: bool | None = None


class TotpRequest(BaseModel):
    totp: str


def _fail(error: BrokerError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.get("")
def list_connections(db: DbSession, manager: Manager) -> dict[str, Any]:
    return {
        "vault_configured": manager.vault_configured(),
        "available": manager.available(),
        "connections": manager.connections(db),
    }


@router.put("/{provider}")
def save_connection(
    provider: str, body: SaveRequest, db: DbSession, manager: Manager
) -> dict[str, Any]:
    try:
        return manager.save(
            db, provider, body.credentials, priority=body.priority, enabled=body.enabled
        )
    except BrokerError as e:
        raise _fail(e) from None


@router.delete("/{provider}")
def delete_connection(provider: str, db: DbSession, manager: Manager) -> dict[str, bool]:
    try:
        manager.delete(db, provider)
    except BrokerError as e:
        raise _fail(e) from None
    return {"deleted": True}


@router.post("/{provider}/disconnect")
def disconnect(provider: str, db: DbSession, manager: Manager) -> dict[str, Any]:
    try:
        return manager.disconnect(db, provider)
    except BrokerError as e:
        raise _fail(e) from None


@router.post("/{provider}/login")
def start_login(provider: str, db: DbSession, manager: Manager) -> dict[str, str]:
    try:
        return {"login_url": manager.start_login(db, provider)}
    except BrokerError as e:
        raise _fail(e) from None


@router.post("/{provider}/totp")
def login_with_totp(
    provider: str, body: TotpRequest, db: DbSession, manager: Manager
) -> dict[str, Any]:
    try:
        return manager.login_with_totp(db, provider, body.totp)
    except BrokerError as e:
        raise _fail(e) from None


def _finish(
    provider: str, state: str, token: str, db: Session, manager: BrokerManager
) -> RedirectResponse:
    try:
        result = manager.complete_redirect(db, provider, state=state, token=token)
        status = result["status"]
    except BrokerError:
        status = "error"
    return RedirectResponse(f"{_RETURN_PATH}?broker={provider}&status={status}", status_code=303)


@callback_router.get("/kite/callback")
def kite_callback(
    db: DbSession, manager: Manager, request_token: str = "", state: str = ""
) -> RedirectResponse:
    return _finish("kite", state, request_token, db, manager)


@callback_router.get("/upstox/callback")
def upstox_callback(
    db: DbSession, manager: Manager, code: str = "", state: str = ""
) -> RedirectResponse:
    return _finish("upstox", state, code, db, manager)
