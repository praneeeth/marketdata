"""Test harness shared by the adapter contract suite and the per-provider tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from marketdata.india import InstrumentRef, MarketDataProvider, ProviderSession, Secret
from marketdata.india.transport import ErrorMapper, Transport

FIXTURES = Path(__file__).parent / "fixtures"
LEAK_CANARY = "CANARY_SECRET_9f3b"

Handler = Callable[[httpx.Request], httpx.Response]


def fixture_text(provider: str, name: str) -> str:
    return (FIXTURES / provider / name).read_text(encoding="utf-8")


def fixture_json(provider: str, name: str) -> Any:
    return json.loads(fixture_text(provider, name))


class Router:
    """A fake provider API: routes ``(METHOD, path)`` to handlers and records requests."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []
        self.fallback: Handler | None = None

    def add(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method, path)] = handler

    def json(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.add(method, path, lambda _r: httpx.Response(status, json=body))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path)) or self.fallback
        if handler is None:
            return httpx.Response(404, json={"message": f"no route {request.url.path}"})
        return handler(request)

    def transport(self, name: str, base_url: str, error_mapper: ErrorMapper | None) -> Transport:
        return Transport(
            name,
            base_url,
            client=httpx.Client(transport=httpx.MockTransport(self)),
            error_mapper=error_mapper,
            sleep=lambda _s: None,
        )


def session_for(provider: str, credential_id: str = "cred-1") -> ProviderSession:
    return ProviderSession(
        provider=provider,
        credential_id=credential_id,
        user_id="user-1",
        api_key=Secret(f"key-{LEAK_CANARY}"),
        access_token=Secret(f"tok-{LEAK_CANARY}"),
        extra={"client_code": Secret(f"cc-{LEAK_CANARY}")},
    )


@dataclass
class Harness:
    """Everything the contract suite needs to exercise one provider offline."""

    name: str
    make: Callable[[Router], MarketDataProvider]
    install_routes: Callable[[Router], None]
    install_expired: Callable[[Router], None] | None  # None: provider has no sessions
    equity: InstrumentRef  # carries this provider's ID
    unresolved: InstrumentRef | None  # has no ID for this provider; None: IDs are derived
    underlying: InstrumentRef
    expiry: date
    start: datetime
    end: datetime
    expected_last_price: str
    extra_session: dict[str, Secret] = field(default_factory=dict)

    def build(self) -> tuple[MarketDataProvider, Router]:
        router = Router()
        self.install_routes(router)
        return self.make(router), router

    def build_expired(self) -> tuple[MarketDataProvider, Router]:
        if self.install_expired is None:
            pytest.skip("provider has no broker session")
        router = Router()
        self.install_expired(router)
        return self.make(router), router
