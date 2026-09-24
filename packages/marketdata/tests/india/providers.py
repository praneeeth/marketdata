"""Harness definitions for each India provider, used by the contract suite."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime

import httpx
from marketdata.india import IST, Exchange, InstrumentRef, yfinance_dev
from marketdata.india import angel as angel_mod
from marketdata.india import kite as kite_mod
from marketdata.india import upstox as upstox_mod

from .fake_yfinance import FakeTicker
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

# --- Upstox ------------------------------------------------------------------------------

UPSTOX_INFY = "NSE_EQ|INE009A01021"
UPSTOX_NIFTY = "NSE_INDEX|Nifty 50"
UPSTOX_NOW = datetime(2026, 9, 23, 16, 0, tzinfo=IST)


def upstox_routes(router: Router) -> None:
    router.json("GET", "/v2/market-quote/quotes", fixture_json("upstox", "quotes.json"))
    router.json(
        "GET",
        "/v3/historical-candle/NSE_EQ|INE009A01021/minutes/5/2026-09-23/2026-09-22",
        fixture_json("upstox", "historical.json"),
    )
    router.json(
        "GET",
        "/v3/historical-candle/intraday/NSE_EQ|INE009A01021/minutes/5",
        fixture_json("upstox", "intraday.json"),
    )
    router.json("GET", "/v2/option/chain", fixture_json("upstox", "option_chain.json"))
    gz = gzip.compress(fixture_text("upstox", "instruments_NSE.json").encode())
    router.add(
        "GET",
        "/market-quote/instruments/exchange/NSE.json.gz",
        lambda _r: httpx.Response(200, content=gz),
    )
    router.json("POST", "/v2/login/authorization/token", fixture_json("upstox", "login_token.json"))


def upstox_expired(router: Router) -> None:
    router.fallback = lambda _r: httpx.Response(
        401, json=fixture_json("upstox", "error_token.json")
    )


UPSTOX = Harness(
    name="upstox",
    make=lambda router: upstox_mod.UpstoxProvider(
        router.transport("upstox", upstox_mod.API_BASE, upstox_mod._error_mapper),
        now=lambda: UPSTOX_NOW,
    ),
    install_routes=upstox_routes,
    install_expired=upstox_expired,
    equity=InstrumentRef(Exchange.NSE, "INFY").with_id("upstox", UPSTOX_INFY),
    unresolved=InstrumentRef(Exchange.NSE, "INFY"),
    underlying=InstrumentRef(Exchange.NSE, "NIFTY 50").with_id("upstox", UPSTOX_NIFTY),
    expiry=date(2026, 10, 27),
    start=datetime(2026, 9, 22, 9, 15, tzinfo=IST),
    end=datetime(2026, 9, 23, 15, 30, tzinfo=IST),
    expected_last_price="1412.95",
)

# --- Angel One ---------------------------------------------------------------------------


def _angel_quote(request: httpx.Request) -> httpx.Response:
    wanted = {
        (ex, tok)
        for ex, toks in json.loads(request.content)["exchangeTokens"].items()
        for tok in toks
    }
    body = fixture_json("angel", "quote.json")
    body["data"]["fetched"] = [
        r for r in body["data"]["fetched"] if (r["exchange"], r["symbolToken"]) in wanted
    ]
    return httpx.Response(200, json=body)


def angel_routes(router: Router) -> None:
    router.add("POST", "/rest/secure/angelbroking/market/v1/quote/", _angel_quote)
    router.json(
        "POST",
        "/rest/secure/angelbroking/historical/v1/getCandleData",
        fixture_json("angel", "candles.json"),
    )
    router.json(
        "GET",
        "/OpenAPI_File/files/OpenAPIScripMaster.json",
        fixture_json("angel", "scrip_master.json"),
    )
    router.json(
        "POST",
        "/rest/auth/angelbroking/user/v1/loginByPassword",
        fixture_json("angel", "login.json"),
    )


def angel_expired(router: Router) -> None:
    # SmartAPI reports token errors inside an HTTP 200 envelope.
    router.fallback = lambda _r: httpx.Response(200, json=fixture_json("angel", "error_token.json"))


ANGEL = Harness(
    name="angel",
    make=lambda router: angel_mod.AngelProvider(
        router.transport("angel", angel_mod.API_BASE, angel_mod._error_mapper)
    ),
    install_routes=angel_routes,
    install_expired=angel_expired,
    equity=InstrumentRef(Exchange.NSE, "INFY").with_id("angel", "1594"),
    unresolved=InstrumentRef(Exchange.NSE, "INFY"),
    underlying=InstrumentRef(Exchange.NSE, "NIFTY 50").with_id("angel", "99926000"),
    expiry=date(2026, 10, 27),
    start=datetime(2026, 9, 23, 9, 15, tzinfo=IST),
    end=datetime(2026, 9, 23, 15, 30, tzinfo=IST),
    expected_last_price="1412.95",
)

# --- yfinance (dev only) --------------------------------------------------------------

DEV_ENV = {"ALLOW_UNOFFICIAL_DATA": "true", "APP_ENV": "development"}

YFINANCE = Harness(
    name="yfinance",
    make=lambda _router: yfinance_dev.YFinanceProvider(DEV_ENV, ticker_factory=FakeTicker),
    install_routes=lambda _router: None,
    install_expired=None,
    equity=InstrumentRef(Exchange.NSE, "INFY"),
    unresolved=None,
    underlying=InstrumentRef(Exchange.NSE, "NIFTY 50"),
    expiry=date(2026, 10, 27),
    start=datetime(2026, 9, 23, 9, 15, tzinfo=IST),
    end=datetime(2026, 9, 23, 15, 30, tzinfo=IST),
    expected_last_price="1412.95",
)

ALL = [KITE, UPSTOX, ANGEL, YFINANCE]
