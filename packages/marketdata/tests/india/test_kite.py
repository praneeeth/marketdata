"""Kite-specific behaviour: login checksum, token expiry, chunking, parsing edge cases."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

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
    Secret,
    SessionExpired,
    kite,
)
from marketdata.india.session import ProviderSession

from .harness import LEAK_CANARY, Router, fixture_json, session_for
from .providers import KITE


def build(router: Router) -> kite.KiteProvider:
    return kite.KiteProvider(router.transport("kite", kite.API_BASE, kite._error_mapper))


# Vector from `printf 'abcreqxyz' | shasum -a 256`, computed outside Python.
def test_checksum_vector() -> None:
    assert (
        kite.checksum("abc", "req", "xyz")
        == "3f7deb265a2394cebf4b1d554874a1788cf291e113a40a9c4f727e0343909474"
    )
    assert kite.checksum("abc", "req", "xyz") == hashlib.sha256(b"abcreqxyz").hexdigest()


def test_login_url() -> None:
    url = kite.login_url(Secret("my_key"), state="abc")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "kite.zerodha.com"
    assert q["v"] == ["3"]
    assert q["api_key"] == ["my_key"]
    assert q["redirect_params"] == ["state=abc"]
    assert "redirect_params" not in kite.login_url(Secret("k"))


@pytest.mark.parametrize(
    ("issued", "expected"),
    [
        (datetime(2026, 9, 23, 8, 30, tzinfo=IST), datetime(2026, 9, 24, 6, 0, tzinfo=IST)),
        (datetime(2026, 9, 23, 5, 59, tzinfo=IST), datetime(2026, 9, 23, 6, 0, tzinfo=IST)),
        (datetime(2026, 9, 23, 6, 0, tzinfo=IST), datetime(2026, 9, 24, 6, 0, tzinfo=IST)),
    ],
)
def test_token_expiry(issued: datetime, expected: datetime) -> None:
    assert kite.token_expiry(issued) == expected


def test_exchange_request_token() -> None:
    router = Router()
    router.json("POST", "/session/token", fixture_json("kite", "session_token.json"))
    session = build(router).exchange_request_token(
        credential_id="c1",
        user_id="u1",
        api_key=Secret("kite_test_key"),
        api_secret=Secret(f"secret-{LEAK_CANARY}"),
        request_token="rt",
        now=datetime(2026, 9, 23, 8, 30, tzinfo=IST),
    )
    assert session.access_token.reveal() == "kite_access_SECRET_value"
    assert session.expires_at == datetime(2026, 9, 24, 6, 0, tzinfo=IST)
    (req,) = router.requests
    form = parse_qs(req.content.decode())
    assert form["checksum"] == [kite.checksum("kite_test_key", "rt", f"secret-{LEAK_CANARY}")]
    assert LEAK_CANARY not in req.content.decode()  # the secret is only ever hashed
    assert LEAK_CANARY not in str(req.url)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json=fixture_json("kite", "error_token.json")),
        httpx.Response(
            400, json={"status": "error", "message": "bad", "error_type": "InputException"}
        ),
    ],
)
def test_exchange_request_token_rejected(response: httpx.Response) -> None:
    router = Router()
    router.add("POST", "/session/token", lambda _r: response)
    with pytest.raises(InvalidCredentials):
        build(router).exchange_request_token(
            credential_id="c",
            user_id="u",
            api_key=Secret("k"),
            api_secret=Secret("s"),
            request_token="rt",
            now=datetime(2026, 9, 23, tzinfo=IST),
        )
    assert len(router.requests) == 1  # logins are never retried


def test_exchange_request_token_without_access_token() -> None:
    router = Router()
    router.json("POST", "/session/token", {"status": "success", "data": {}})
    with pytest.raises(BadResponse, match="access_token"):
        build(router).exchange_request_token(
            credential_id="c",
            user_id="u",
            api_key=Secret("k"),
            api_secret=Secret("s"),
            request_token="rt",
            now=datetime(2026, 9, 23, tzinfo=IST),
        )


def test_quote_fields_and_auth_header() -> None:
    p, router = KITE.build()
    (q,) = p.quotes(session_for("kite"), [InstrumentRef(Exchange.NSE, "INFY")])
    assert q.prev_close == Decimal("1389.65")
    assert q.change == Decimal("23.30")
    assert q.change_pct == Decimal("1.68")
    assert q.upper_circuit == Decimal("1528.6")
    assert q.lower_circuit == Decimal("1250.7")
    assert q.volume == 7360198
    assert q.oi is None or q.oi == 0
    assert q.exchange_ts == datetime(2026, 9, 23, 15, 45, 56, tzinfo=IST)
    assert q.instrument.id_for("kite") == "408065"  # learned from the response
    (req,) = router.requests
    assert req.headers["X-Kite-Version"] == "3"
    assert req.headers["Authorization"] == f"token key-{LEAK_CANARY}:tok-{LEAK_CANARY}"


def test_quote_skips_missing_instruments() -> None:
    p, _ = KITE.build()
    assert p.quotes(session_for("kite"), [InstrumentRef(Exchange.NSE, "NOPE")]) == []


def test_quotes_are_batched() -> None:
    p, router = KITE.build()
    refs = [InstrumentRef(Exchange.NSE, f"S{i}") for i in range(kite.QUOTE_BATCH + 1)]
    p.quotes(session_for("kite"), refs)
    assert [len(r.url.params.get_list("i")) for r in router.requests] == [kite.QUOTE_BATCH, 1]


def test_not_logged_in() -> None:
    p, router = KITE.build()
    empty = ProviderSession(provider="kite", credential_id="c", user_id="u")
    with pytest.raises(SessionExpired, match="not logged in"):
        p.quotes(empty, [KITE.equity])
    assert router.requests == []


def test_bad_envelope() -> None:
    router = Router()
    router.json("GET", "/quote", {"status": "error"})
    with pytest.raises(BadResponse, match="envelope"):
        build(router).quotes(session_for("kite"), [KITE.equity])
    router2 = Router()
    router2.json("GET", "/quote", {"status": "success", "data": []})
    with pytest.raises(BadResponse, match="data"):
        build(router2).quotes(session_for("kite"), [KITE.equity])


def test_error_mapper() -> None:
    def resp(status: int, body: object) -> httpx.Response:
        return httpx.Response(status, json=body)

    assert isinstance(
        kite._error_mapper(resp(403, {"error_type": "TokenException"})), SessionExpired
    )
    assert isinstance(
        kite._error_mapper(resp(403, {"error_type": "PermissionException", "message": "no"})),
        BadResponse,
    )
    assert kite._error_mapper(resp(500, {"error_type": "GeneralException"})) is None
    assert kite._error_mapper(resp(500, [1])) is None
    assert kite._error_mapper(httpx.Response(500, text="<html>")) is None


def test_candles_are_chunked_by_max_span() -> None:
    p, router = KITE.build()
    start = datetime(2026, 1, 1, tzinfo=IST)
    end = start + timedelta(days=130)
    p.candles(session_for("kite"), KITE.equity, Interval.MINUTE_5, start, end)
    assert len(router.requests) == 2  # 100-day limit for 5-minute candles
    first, second = (r.url.params for r in router.requests)
    assert first["from"] == "2026-01-01 00:00:00"
    assert first["oi"] == "1"
    assert second["to"] == end.strftime("%Y-%m-%d %H:%M:%S")


def test_candles_dedupe_and_skip_bad_rows() -> None:
    router = Router()
    router.json(
        "GET",
        "/instruments/historical/408065/day",
        {
            "status": "success",
            "data": {
                "candles": [
                    ["2026-09-22T00:00:00+0530", 1, 2, 0.5, 1.5, 10],
                    ["2026-09-22T00:00:00+0530", 1, 2, 0.5, 1.5, 10],
                    ["bad", 1, 2, 3, 4, 5],
                    ["2026-09-23T00:00:00+0530", 1, 2, 0.5, None, 10],
                    [1, 2],
                ]
            },
        },
    )
    candles = build(router).candles(
        session_for("kite"),
        KITE.equity,
        Interval.DAY_1,
        datetime(2026, 9, 1),  # noqa: DTZ001 - naive input is treated as IST
        datetime(2026, 9, 30, tzinfo=IST),
    )
    assert len(candles) == 1
    assert candles[0].oi is None


def test_candles_without_candles_key() -> None:
    router = Router()
    router.json("GET", "/instruments/historical/408065/day", {"status": "success", "data": {}})
    with pytest.raises(BadResponse, match="candles"):
        build(router).candles(
            session_for("kite"), KITE.equity, Interval.DAY_1, KITE.start, KITE.end
        )


def test_instrument_parsing() -> None:
    p, _ = KITE.build()
    nse = {i.tradingsymbol: i for i in p.instruments(session_for("kite"), Exchange.NSE)}
    assert nse["INFY"].instrument_type is InstrumentType.EQUITY
    assert nse["INFY"].tick_size == Decimal("0.05")
    assert nse["INFY"].strike is None
    assert nse["INFY"].underlying is None
    assert nse["NIFTY 50"].instrument_type is InstrumentType.INDEX
    nfo = {i.tradingsymbol: i for i in p.instruments(session_for("kite"), Exchange.NFO)}
    assert "CRUDEOIL26OCTFUT" not in nfo  # MCX rows are dropped
    ce = nfo["NIFTY26OCT25000CE"]
    assert ce.instrument_type is InstrumentType.CALL
    assert ce.expiry == date(2026, 10, 27)
    assert ce.strike == Decimal("25000.0")
    assert ce.lot_size == 75
    assert ce.underlying == "NIFTY"
    assert ce.key == "NFO:NIFTY:2026-10-27:25000:CE"
    assert nfo["NIFTY26OCTFUT"].instrument_type is InstrumentType.FUTURE


def test_instrument_rows_with_missing_fields_are_skipped() -> None:
    router = Router()
    csv_text = (
        "instrument_token,tradingsymbol,name,expiry,strike,tick_size,lot_size,"
        "instrument_type,segment,exchange\n"
        ",NOTOKEN,X,,0,0.05,1,EQ,NSE,NSE\n"
        "1,BADLOT,X,,0,0.05,x,EQ,NSE,NSE\n"
        "2,ODD,X,,0,0.05,1,XX,NSE,NSE\n"
    )
    router.add("GET", "/instruments/NSE", lambda _r: httpx.Response(200, text=csv_text))
    items = list(build(router).instruments(session_for("kite"), Exchange.NSE))
    assert [(i.tradingsymbol, i.instrument_type) for i in items] == [("ODD", InstrumentType.OTHER)]


def test_option_chain_contents() -> None:
    p, _ = KITE.build()
    chain = p.option_chain(session_for("kite"), KITE.underlying, KITE.expiry)
    assert chain.spot == Decimal("25010.5")
    assert [r.strike for r in chain.rows] == [Decimal("25000.0"), Decimal("25100.0")]
    first = chain.rows[0]
    assert first.call is not None
    assert first.put is not None
    assert first.call.last_price == Decimal("310.25")
    assert first.put.oi == 510000
    assert chain.rows[1].put is None  # only a call is listed at 25100


def test_option_chain_on_bse_uses_bfo() -> None:
    p, router = KITE.build()
    router.add("GET", "/instruments/BFO", lambda _r: httpx.Response(200, text="exchange\n"))
    chain = p.option_chain(
        session_for("kite"), InstrumentRef(Exchange.BSE, "SENSEX"), date(2026, 10, 29)
    )
    assert chain.rows == ()
    assert chain.spot is None


def test_option_chain_reuses_the_cached_instrument_master() -> None:
    """Review #7: repeated chains must not re-download the F&O master."""
    from datetime import timedelta as _td

    from marketdata.india.instrument_cache import InstrumentCache

    router = Router()
    from .providers import kite_routes

    kite_routes(router)
    cache = InstrumentCache(_td(hours=12), lambda: datetime(2026, 9, 23, tzinfo=IST))
    provider = kite.KiteProvider(
        router.transport("kite", kite.API_BASE, kite._error_mapper), instrument_cache=cache
    )
    for _ in range(3):
        provider.option_chain(session_for("kite"), KITE.underlying, KITE.expiry)
    downloads = [r for r in router.requests if r.url.path == "/instruments/NFO"]
    assert len(downloads) == 1
