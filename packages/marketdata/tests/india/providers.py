"""Harness definitions for each India provider, used by the contract suite."""

from __future__ import annotations

from datetime import date, datetime

import httpx
from marketdata.india import IST, Exchange, InstrumentRef
from marketdata.india import kite as kite_mod

from .harness import Harness, Router, fixture_json, fixture_text

# --- Kite --------------------------------------------------------------------------------


def _kite_quote(request: httpx.Request) -> httpx.Response:
    wanted = request.url.params.get_list("i")
    body = fixture_json("kite", "quote.json")
    body["data"] = {k: v for k, v in body["data"].items() if k in wanted}
    return httpx.Response(200, json=body)


def kite_routes(router: Router) -> None:
    router.add("GET", "/quote", _kite_quote)
    router.json(
        "GET", "/instruments/historical/408065/5minute", fixture_json("kite", "historical.json")
    )
    for ex in ("NSE", "NFO"):
        router.add(
            "GET",
            f"/instruments/{ex}",
            lambda _r, ex=ex: httpx.Response(
                200, text=fixture_text("kite", f"instruments_{ex}.csv")
            ),
        )
    router.json("POST", "/session/token", fixture_json("kite", "session_token.json"))


def kite_expired(router: Router) -> None:
    router.fallback = lambda _r: httpx.Response(403, json=fixture_json("kite", "error_token.json"))


KITE = Harness(
    name="kite",
    make=lambda router: kite_mod.KiteProvider(
        router.transport("kite", kite_mod.API_BASE, kite_mod._error_mapper)
    ),
    install_routes=kite_routes,
    install_expired=kite_expired,
    equity=InstrumentRef(Exchange.NSE, "INFY").with_id("kite", "408065"),
    unresolved=InstrumentRef(Exchange.NSE, "INFY"),
    underlying=InstrumentRef(Exchange.NSE, "NIFTY 50"),
    expiry=date(2026, 10, 27),
    start=datetime(2026, 9, 23, 9, 15, tzinfo=IST),
    end=datetime(2026, 9, 23, 15, 30, tzinfo=IST),
    expected_last_price="1412.95",
)

ALL = [KITE]
