"""Angel One-specific behaviour: TOTP login, 200-status errors, paise conversions."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from marketdata.india import (
    IST,
    BadResponse,
    Exchange,
    InstrumentRef,
    InstrumentType,
    Interval,
    InvalidCredentials,
    RateLimited,
    Secret,
    SessionExpired,
    angel,
)
from marketdata.india.session import ProviderSession

from .harness import LEAK_CANARY, Router, fixture_json, session_for
from .providers import ANGEL


def build(router: Router) -> angel.AngelProvider:
    return angel.AngelProvider(router.transport("angel", angel.API_BASE, angel._error_mapper))


def login(router: Router) -> ProviderSession:
    return build(router).login(
        credential_id="c1",
        user_id="u1",
        api_key=Secret("pk"),
        client_code=Secret("A123"),
        pin=Secret(f"pin-{LEAK_CANARY}"),
        totp="123456",
        now=datetime(2026, 9, 23, 8, 45, tzinfo=IST),
    )


def test_login_uses_typed_totp_and_keeps_no_seed() -> None:
    router = Router()
    router.json(
        "POST",
        "/rest/auth/angelbroking/user/v1/loginByPassword",
        fixture_json("angel", "login.json"),
    )
    session = login(router)
    assert session.access_token.reveal() == "angel_jwt_SECRET_value"
    assert session.expires_at == datetime(2026, 9, 24, 0, 0, tzinfo=IST)
    assert set(session.extra) == {"client_code"}  # no PIN, TOTP or seed is kept
    (req,) = router.requests
    assert json.loads(req.content)["totp"] == "123456"
    assert req.headers["X-PrivateKey"] == "pk"
    assert LEAK_CANARY not in str(req.url)


@pytest.mark.parametrize(
    "body",
    [
        {"status": False, "message": "Invalid totp", "errorcode": "AB1050", "data": None},
        fixture_json("angel", "error_token.json"),
    ],
)
def test_login_rejected(body: object) -> None:
    router = Router()
    router.json("POST", "/rest/auth/angelbroking/user/v1/loginByPassword", body)
    with pytest.raises(InvalidCredentials):
        login(router)
    assert len(router.requests) == 1


def test_login_without_token() -> None:
    router = Router()
    router.json(
        "POST",
        "/rest/auth/angelbroking/user/v1/loginByPassword",
        {"status": True, "data": {"feedToken": "x"}},
    )
    with pytest.raises(BadResponse, match="jwtToken"):
        login(router)


def test_quote_fields_and_headers() -> None:
    p, router = ANGEL.build()
    (q,) = p.quotes(session_for("angel"), [ANGEL.equity])
    assert q.prev_close == Decimal("1389.65")
    assert q.change_pct == Decimal("1.68")
    assert q.exchange_ts == datetime(2026, 9, 23, 15, 45, 56, tzinfo=IST)
    assert q.last_trade_ts == datetime(2026, 9, 23, 15, 29, 59, tzinfo=IST)
    assert q.upper_circuit == Decimal("1528.6")
    (req,) = router.requests
    assert json.loads(req.content) == {"mode": "FULL", "exchangeTokens": {"NSE": ["1594"]}}
    assert req.headers["Authorization"] == f"Bearer tok-{LEAK_CANARY}"
    assert req.headers["X-UserType"] == "USER"


def test_quotes_batch_at_fifty() -> None:
    p, router = ANGEL.build()
    refs = [InstrumentRef(Exchange.NSE, f"S{i}").with_id("angel", str(i)) for i in range(51)]
    p.quotes(session_for("angel"), refs)
    sizes = [len(json.loads(r.content)["exchangeTokens"]["NSE"]) for r in router.requests]
    assert sizes == [50, 1]


def test_quote_malformed() -> None:
    router = Router()
    router.json("POST", "/rest/secure/angelbroking/market/v1/quote/", {"status": True, "data": {}})
    with pytest.raises(BadResponse, match="fetched"):
        build(router).quotes(session_for("angel"), [ANGEL.equity])
    router2 = Router()
    router2.json(
        "POST",
        "/rest/secure/angelbroking/market/v1/quote/",
        {"status": True, "data": {"fetched": ["x", {"exchange": "NSE", "symbolToken": "9"}]}},
    )
    assert build(router2).quotes(session_for("angel"), [ANGEL.equity]) == []
    with pytest.raises(SessionExpired, match="not logged in"):
        build(Router()).quotes(
            ProviderSession(provider="angel", credential_id="c", user_id="u"), [ANGEL.equity]
        )


def test_envelope_errors() -> None:
    for body, error in (
        ({"status": False, "errorcode": "AG8002", "message": "Token expired"}, SessionExpired),
        (
            {
                "status": False,
                "errorcode": "",
                "message": "Access denied because of exceeding access rate",
            },
            RateLimited,
        ),
        ({"status": False, "errorcode": "AB2001", "message": "Internal error"}, BadResponse),
        ({"status": False}, BadResponse),
        ([1, 2], BadResponse),
    ):
        router = Router()
        router.json("POST", "/rest/secure/angelbroking/market/v1/quote/", body)
        with pytest.raises(error):
            build(router).quotes(session_for("angel"), [ANGEL.equity])


def test_error_mapper_for_http_errors() -> None:
    expired = httpx.Response(401, json=fixture_json("angel", "error_token.json"))
    assert isinstance(angel._error_mapper(expired), SessionExpired)
    assert angel._error_mapper(httpx.Response(500, json={"status": True})) is None
    assert angel._error_mapper(httpx.Response(500, text="<html>")) is None


def test_candles_request_and_chunking() -> None:
    p, router = ANGEL.build()
    p.candles(session_for("angel"), ANGEL.equity, Interval.MINUTE_5, ANGEL.start, ANGEL.end)
    body = json.loads(router.requests[0].content)
    assert body == {
        "exchange": "NSE",
        "symboltoken": "1594",
        "interval": "FIVE_MINUTE",
        "fromdate": "2026-09-23 09:15",
        "todate": "2026-09-23 15:30",
    }
    router.requests.clear()
    start = datetime(2026, 1, 1, tzinfo=IST)
    p.candles(
        session_for("angel"), ANGEL.equity, Interval.MINUTE_1, start, start + timedelta(days=45)
    )
    assert len(router.requests) == 2  # 30-day limit for 1-minute candles


def test_candles_malformed() -> None:
    router = Router()
    router.json(
        "POST",
        "/rest/secure/angelbroking/historical/v1/getCandleData",
        {"status": True, "data": {"x": 1}},
    )
    with pytest.raises(BadResponse, match="candle list"):
        build(router).candles(
            session_for("angel"), ANGEL.equity, Interval.DAY_1, ANGEL.start, ANGEL.end
        )
    router2 = Router()
    router2.json(
        "POST",
        "/rest/secure/angelbroking/historical/v1/getCandleData",
        {
            "status": True,
            "data": [
                ["bad", 1, 1, 1, 1, 1],
                [1],
                ["2026-09-23T09:15:00+05:30", 1, 2, 0.5, None, 3],
            ],
        },
    )
    assert (
        build(router2).candles(
            session_for("angel"), ANGEL.equity, Interval.DAY_1, ANGEL.start, ANGEL.end
        )
        == []
    )


def test_instrument_parsing() -> None:
    p, _ = ANGEL.build()
    nse = {i.tradingsymbol: i for i in p.instruments(session_for("angel"), Exchange.NSE)}
    assert set(nse) == {"INFY", "NIFTY 50", "SOMECO-BE"}  # "-EQ" series suffix removed
    assert nse["INFY"].tick_size == Decimal("0.05")
    assert nse["INFY"].instrument_type is InstrumentType.EQUITY
    assert nse["NIFTY 50"].instrument_type is InstrumentType.INDEX
    nfo = {i.tradingsymbol: i for i in p.instruments(session_for("angel"), Exchange.NFO)}
    ce = nfo["NIFTY27OCT2625000CE"]
    assert ce.strike == Decimal("25000")  # from paise
    assert ce.expiry == date(2026, 10, 27)
    assert ce.key == "NFO:NIFTY:2026-10-27:25000:CE"  # same key as Kite and Upstox
    assert nfo["NIFTY27OCT26FUT"].instrument_type is InstrumentType.FUTURE
    assert nfo["NIFTY27OCT26FUT"].strike is None


def test_instrument_edge_rows() -> None:
    rows = [
        {
            "token": "1",
            "symbol": "X",
            "lotsize": "1",
            "tick_size": "5",
            "exch_seg": "NSE",
            "instrumenttype": "OPTSTK",
        },
        {"token": "2", "symbol": "Y", "lotsize": "0", "tick_size": "5", "exch_seg": "NSE"},
        {
            "token": "3",
            "symbol": "Z",
            "lotsize": "1",
            "tick_size": "5",
            "exch_seg": "NSE",
            "instrumenttype": "WEIRD",
            "expiry": "31FEB2026",
        },
        {"token": "", "symbol": "W", "lotsize": "1", "tick_size": "5", "exch_seg": "NSE"},
        "junk",
    ]
    router = Router()
    router.json("GET", "/OpenAPI_File/files/OpenAPIScripMaster.json", rows)
    items = list(build(router).instruments(session_for("angel"), Exchange.NSE))
    assert [(i.tradingsymbol, i.instrument_type, i.expiry) for i in items] == [
        ("Z", InstrumentType.OTHER, None)
    ]
    router2 = Router()
    router2.json("GET", "/OpenAPI_File/files/OpenAPIScripMaster.json", {"x": 1})
    with pytest.raises(BadResponse, match="scrip master"):
        list(build(router2).instruments(session_for("angel"), Exchange.NSE))


def test_option_chain_contents() -> None:
    p, _ = ANGEL.build()
    chain = p.option_chain(session_for("angel"), ANGEL.underlying, ANGEL.expiry)
    assert chain.spot == Decimal("25010.5")
    assert [r.strike for r in chain.rows] == [Decimal("25000"), Decimal("25100")]
    call = chain.rows[0].call
    assert call is not None
    assert call.oi == 450000
    upper = chain.rows[1].call
    assert upper is not None
    assert upper.last_price is None  # listed contract with no quote


def test_option_chain_without_underlying_token_has_no_spot() -> None:
    p, _ = ANGEL.build()
    chain = p.option_chain(
        session_for("angel"), InstrumentRef(Exchange.NSE, "NIFTY 50"), ANGEL.expiry
    )
    assert chain.spot is None
    assert chain.rows


def test_angel_timestamp_parsing() -> None:
    assert angel._angel_ts("bad") is None
    assert angel._angel_ts(None) is None
    assert angel._expiry(None) is None
