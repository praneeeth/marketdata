"""Upstox-specific behaviour: OAuth code exchange, intraday split, paise tick sizes."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from marketdata.india import (
    IST,
    BadResponse,
    Exchange,
    InstrumentNotResolved,
    InstrumentRef,
    InstrumentType,
    Interval,
    InvalidCredentials,
    Secret,
    SessionExpired,
    upstox,
)
from marketdata.india.session import ProviderSession

from .harness import LEAK_CANARY, Router, fixture_json, session_for
from .providers import UPSTOX, UPSTOX_INFY, UPSTOX_NOW


def build(router: Router, now: datetime = UPSTOX_NOW) -> upstox.UpstoxProvider:
    return upstox.UpstoxProvider(
        router.transport("upstox", upstox.API_BASE, upstox._error_mapper), now=lambda: now
    )


def test_authorize_url() -> None:
    url = upstox.authorize_url(Secret("cid"), "https://app.example/cb", "st8")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.path == "/v2/login/authorization/dialog"
    assert q == {
        "response_type": ["code"],
        "client_id": ["cid"],
        "redirect_uri": ["https://app.example/cb"],
        "state": ["st8"],
    }


@pytest.mark.parametrize(
    ("issued", "expected"),
    [
        (datetime(2026, 9, 23, 9, 0, tzinfo=IST), datetime(2026, 9, 24, 3, 30, tzinfo=IST)),
        (datetime(2026, 9, 23, 3, 0, tzinfo=IST), datetime(2026, 9, 23, 3, 30, tzinfo=IST)),
    ],
)
def test_token_expiry(issued: datetime, expected: datetime) -> None:
    assert upstox.token_expiry(issued) == expected


def test_exchange_code() -> None:
    router = Router()
    router.json("POST", "/v2/login/authorization/token", fixture_json("upstox", "login_token.json"))
    session = build(router).exchange_code(
        credential_id="c1",
        user_id="u1",
        client_id=Secret("cid"),
        client_secret=Secret(f"sec-{LEAK_CANARY}"),
        redirect_uri="https://app.example/cb",
        code="the-code",
        now=datetime(2026, 9, 23, 9, tzinfo=IST),
    )
    assert session.access_token.reveal() == "upstox_access_SECRET_value"
    assert session.expires_at == datetime(2026, 9, 24, 3, 30, tzinfo=IST)
    (req,) = router.requests
    form = parse_qs(req.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["the-code"]
    assert LEAK_CANARY not in str(req.url)  # the secret goes in the POST body only


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, fixture_json("upstox", "error_token.json"), InvalidCredentials),
        (
            400,
            {"status": "error", "errors": [{"errorCode": "UDAPI100069", "message": "x"}]},
            InvalidCredentials,
        ),
        (200, {"user_id": "x"}, BadResponse),
    ],
)
def test_exchange_code_failures(status: int, body: object, error: type[Exception]) -> None:
    router = Router()
    router.json("POST", "/v2/login/authorization/token", body, status=status)
    with pytest.raises(error):
        build(router).exchange_code(
            credential_id="c",
            user_id="u",
            client_id=Secret("cid"),
            client_secret=Secret("s"),
            redirect_uri="https://app.example/cb",
            code="x",
            now=UPSTOX_NOW,
        )


def test_quote_fields_and_header() -> None:
    p, router = UPSTOX.build()
    (q,) = p.quotes(session_for("upstox"), [UPSTOX.equity])
    assert q.change == Decimal("23.3")
    assert q.prev_close == Decimal("1389.65")
    assert q.change_pct == Decimal("1.68")
    assert q.last_trade_ts == datetime(2026, 9, 23, 15, 29, 59, tzinfo=IST)
    assert q.oi == 0
    (req,) = router.requests
    assert req.url.params["instrument_key"] == UPSTOX_INFY
    assert req.headers["Authorization"] == f"Bearer tok-{LEAK_CANARY}"


def test_quotes_are_batched_and_deduplicated() -> None:
    p, router = UPSTOX.build()
    refs = [
        InstrumentRef(Exchange.NSE, f"S{i}").with_id("upstox", f"NSE_EQ|K{i}")
        for i in range(upstox.QUOTE_BATCH + 1)
    ]
    p.quotes(session_for("upstox"), [*refs, refs[0]])
    sizes = [len(r.url.params["instrument_key"].split(",")) for r in router.requests]
    assert sizes == [upstox.QUOTE_BATCH, 1]


def test_quote_errors() -> None:
    router = Router()
    router.json("GET", "/v2/market-quote/quotes", {"status": "success", "data": [1]})
    with pytest.raises(BadResponse, match="data object"):
        build(router).quotes(session_for("upstox"), [UPSTOX.equity])
    router2 = Router()
    router2.json("GET", "/v2/market-quote/quotes", {"status": "success", "data": {"x": 1, "y": {}}})
    assert build(router2).quotes(session_for("upstox"), [UPSTOX.equity]) == []
    with pytest.raises(InstrumentNotResolved):
        build(Router()).quotes(session_for("upstox"), [UPSTOX.unresolved])
    with pytest.raises(SessionExpired, match="not logged in"):
        build(Router()).quotes(
            ProviderSession(provider="upstox", credential_id="c", user_id="u"), [UPSTOX.equity]
        )


def test_candles_merge_historical_and_intraday() -> None:
    p, router = UPSTOX.build()
    candles = p.candles(
        session_for("upstox"), UPSTOX.equity, Interval.MINUTE_5, UPSTOX.start, UPSTOX.end
    )
    assert [c.ts.day for c in candles] == [22, 22, 22, 23, 23]
    assert [r.url.path.split("/")[3] for r in router.requests] == [
        "NSE_EQ|INE009A01021",
        "intraday",
    ]
    assert "%7C" in str(router.requests[0].url)  # the "|" in the key is URL-encoded


def test_candles_in_the_past_skip_intraday() -> None:
    router = Router()
    router.json(
        "GET",
        "/v3/historical-candle/NSE_EQ|INE009A01021/days/1/2026-09-22/2026-09-01",
        fixture_json("upstox", "historical.json"),
    )
    candles = build(router).candles(
        session_for("upstox"),
        UPSTOX.equity,
        Interval.DAY_1,
        datetime(2026, 9, 1, tzinfo=IST),
        datetime(2026, 9, 22, 23, 59, tzinfo=IST),
    )
    assert len(candles) == 3
    assert len(router.requests) == 1


def test_candles_chunk_long_ranges() -> None:
    router = Router()
    router.fallback = lambda _r: httpx.Response(
        200, json={"status": "success", "data": {"candles": []}}
    )
    start = datetime(2026, 1, 1, tzinfo=IST)
    build(router).candles(
        session_for("upstox"), UPSTOX.equity, Interval.MINUTE_1, start, start + timedelta(days=60)
    )
    assert len(router.requests) == 3  # 28-day chunks for 1-minute candles


def test_candles_without_candles() -> None:
    router = Router()
    router.fallback = lambda _r: httpx.Response(200, json={"status": "success", "data": {}})
    with pytest.raises(BadResponse, match="candles"):
        build(router).candles(
            session_for("upstox"), UPSTOX.equity, Interval.DAY_1, UPSTOX.start, UPSTOX.end
        )


def test_instrument_parsing() -> None:
    p, _ = UPSTOX.build()
    items = {i.tradingsymbol: i for i in p.instruments(session_for("upstox"), Exchange.NSE)}
    assert set(items) == {"INFY", "NIFTY 50"}
    infy = items["INFY"]
    assert infy.tick_size == Decimal("0.05")  # 5 paise
    assert infy.isin == "INE009A01021"
    assert items["NIFTY 50"].instrument_type is InstrumentType.INDEX
    assert items["NIFTY 50"].lot_size == 1
    nfo = {i.tradingsymbol: i for i in p.instruments(session_for("upstox"), Exchange.NFO)}
    ce = nfo["NIFTY 25000 CE 27 OCT 26"]
    assert ce.expiry == date(2026, 10, 27)
    assert ce.key == "NFO:NIFTY:2026-10-27:25000:CE"  # matches Kite's key for the same contract
    assert nfo["NIFTY FUT 27 OCT 26"].strike is None


def test_instrument_file_variants() -> None:
    rows = [
        {
            "segment": "NSE_EQ",
            "instrument_key": "k1",
            "lot_size": 1,
            "tick_size": 5,
            "trading_symbol": "A",
            "instrument_type": "EQ",
        },
        {
            "segment": "NSE_EQ",
            "instrument_key": "k2",
            "lot_size": 0,
            "tick_size": 5,
            "trading_symbol": "B",
            "instrument_type": "EQ",
        },
        {"segment": "NSE_EQ", "instrument_key": "k3", "lot_size": 1, "trading_symbol": "C"},
        {
            "segment": "NSE_EQ",
            "instrument_key": "k4",
            "lot_size": 1,
            "tick_size": 5,
            "trading_symbol": "",
        },
        "junk",
    ]
    router = Router()
    # Plain (already decompressed) JSON is accepted too.
    router.fallback = lambda _r: httpx.Response(200, content=json.dumps(rows).encode())
    items = list(build(router).instruments(session_for("upstox"), Exchange.NSE))
    assert [i.tradingsymbol for i in items] == ["A"]
    for content, match in ((b"not json", "not JSON"), (gzip.compress(b"{}"), "not a list")):
        r = Router()
        r.fallback = lambda _r, c=content: httpx.Response(200, content=c)
        with pytest.raises(BadResponse, match=match):
            list(build(r).instruments(session_for("upstox"), Exchange.NSE))


def test_option_chain_contents() -> None:
    p, router = UPSTOX.build()
    chain = p.option_chain(session_for("upstox"), UPSTOX.underlying, UPSTOX.expiry)
    assert chain.spot == Decimal("25010.5")
    assert [r.strike for r in chain.rows] == [Decimal("25000.0"), Decimal("25100.0")]
    call = chain.rows[0].call
    put = chain.rows[0].put
    assert call is not None
    assert put is not None
    assert call.delta == Decimal("0.52")
    assert call.instrument.exchange is Exchange.NFO
    assert put.bid is None  # 0 means no bid
    assert chain.rows[1].put is None
    assert router.requests[0].url.params["expiry_date"] == "2026-10-27"


def test_option_chain_malformed() -> None:
    router = Router()
    router.json(
        "GET",
        "/v2/option/chain",
        {
            "status": "success",
            "data": [
                "x",
                {"strike_price": None},
                {"strike_price": 1, "call_options": {"instrument_key": ""}},
            ],
        },
    )
    chain = build(router).option_chain(
        session_for("upstox"),
        InstrumentRef(Exchange.BSE, "SENSEX").with_id("upstox", "BSE_INDEX|SENSEX"),
        date(2026, 10, 29),
    )
    assert len(chain.rows) == 1
    assert chain.rows[0].call is None
    router2 = Router()
    router2.json("GET", "/v2/option/chain", {"status": "success", "data": {}})
    with pytest.raises(BadResponse, match="not a list"):
        build(router2).option_chain(session_for("upstox"), UPSTOX.underlying, UPSTOX.expiry)


def test_error_mapper() -> None:
    def resp(status: int, body: object) -> httpx.Response:
        return httpx.Response(status, json=body)

    expired = fixture_json("upstox", "error_token.json")
    assert isinstance(upstox._error_mapper(resp(400, expired)), SessionExpired)
    other = {"errors": [{"error_code": "UDAPI1", "message": "bad input"}]}
    mapped = upstox._error_mapper(resp(400, other))
    assert isinstance(mapped, BadResponse)
    assert "UDAPI1" in str(mapped)
    assert isinstance(
        upstox._error_mapper(resp(401, {"errors": [{"message": "m"}]})), SessionExpired
    )
    assert "m" in str(upstox._error_mapper(resp(400, {"errors": [{"message": "m"}]})))
    assert upstox._error_mapper(resp(400, {"errors": []})) is None
    assert upstox._error_mapper(resp(400, [1])) is None
    assert upstox._error_mapper(httpx.Response(500, text="<html>")) is None
