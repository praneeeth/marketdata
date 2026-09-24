"""Core India types, secret handling, parsing and transport behaviour."""

from __future__ import annotations

import logging
import pickle
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from marketdata.india import (
    IST,
    BadResponse,
    Candle,
    Exchange,
    Instrument,
    InstrumentRef,
    InstrumentType,
    Interval,
    ProviderSession,
    ProviderUnavailable,
    Quote,
    RateLimited,
    Secret,
    SessionExpired,
)
from marketdata.india import _parse as p
from marketdata.india.transport import Throttle, Transport, provider_message
from marketdata.india.types import DataQuality

SECRET = "sk_live_DO_NOT_LEAK_123"
NAIVE = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately naive, to test rejection


def make_session(credential_id: str = "cred-1") -> ProviderSession:
    return ProviderSession(
        provider="kite",
        credential_id=credential_id,
        user_id="u1",
        api_key=Secret("apikey-" + SECRET),
        access_token=Secret(SECRET),
        extra={"pin": Secret(SECRET)},
    )


# --- secrets -----------------------------------------------------------------------------


def test_secret_never_renders() -> None:
    s = Secret(SECRET)
    percent = "%s" % (s,)  # noqa: UP031 - the formatting ``logging`` uses
    for text in (repr(s), str(s), f"{s}", f"{s!r}", percent, f"{[s]}", f"{ {'k': s} }"):
        assert SECRET not in text
    assert s.reveal() == SECRET


def test_secret_equality_and_truthiness() -> None:
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")
    assert Secret("a").__eq__("a") is NotImplemented
    assert not Secret("")
    assert hash(Secret("a")) == hash(Secret("b"))  # hash reveals nothing


def test_secret_and_session_refuse_pickling() -> None:
    with pytest.raises(TypeError):
        pickle.dumps(Secret(SECRET))
    with pytest.raises(TypeError):
        pickle.dumps(make_session())


def test_session_repr_and_logging_hide_credentials(caplog: pytest.LogCaptureFixture) -> None:
    session = make_session()
    caplog.set_level(logging.DEBUG)
    logging.getLogger("t").info("session=%s %r", session, session)
    logging.getLogger("t").info("fields=%s", vars(session) if hasattr(session, "__dict__") else "")
    text = repr(session) + str(session) + caplog.text
    assert SECRET not in text
    assert "cred-1" in repr(session)


def test_session_validation() -> None:
    with pytest.raises(ValueError, match="credential_id"):
        ProviderSession(provider="kite", credential_id="", user_id="u")
    with pytest.raises(ValueError, match="timezone"):
        ProviderSession(
            provider="kite",
            credential_id="c",
            user_id="u",
            expires_at=NAIVE,
        )
    s = ProviderSession(
        provider="kite",
        credential_id="c",
        user_id="u",
        expires_at=datetime(2026, 1, 1, 6, tzinfo=IST),
    )
    assert not s.is_expired(datetime(2026, 1, 1, 5, tzinfo=IST))
    assert s.is_expired(datetime(2026, 1, 1, 6, tzinfo=IST))
    assert not make_session().is_expired(datetime.now(UTC))


# --- types -------------------------------------------------------------------------------


def test_instrument_ref_ids() -> None:
    ref = InstrumentRef(Exchange.NSE, "INFY")
    assert ref.id_for("kite") is None
    ref2 = ref.with_id("kite", "408065").with_id("upstox", "NSE_EQ|INE009A01021")
    ref3 = ref2.with_id("kite", "1")
    assert ref2.id_for("kite") == "408065"
    assert ref3.id_for("kite") == "1"
    assert ref3.id_for("upstox") == "NSE_EQ|INE009A01021"
    assert ref.display == "NSE:INFY"


def test_instrument_key_is_provider_independent_for_derivatives() -> None:
    common = {
        "exchange": Exchange.NFO,
        "name": "NIFTY",
        "instrument_type": InstrumentType.CALL,
        "segment": "NFO-OPT",
        "lot_size": 75,
        "tick_size": Decimal("0.05"),
        "expiry": date(2026, 10, 27),
        "underlying": "NIFTY",
    }
    kite = Instrument(tradingsymbol="NIFTY26OCT25000CE", strike=Decimal("25000"), **common)  # type: ignore[arg-type]
    upstox = Instrument(
        tradingsymbol="NIFTY 25000 CE 27 OCT 26",
        strike=Decimal("25000.00"),
        **common,  # type: ignore[arg-type]
    )
    assert kite.key == upstox.key == "NFO:NIFTY:2026-10-27:25000:CE"
    eq = Instrument(
        Exchange.NSE, "INFY", "INFOSYS", InstrumentType.EQUITY, "NSE", 1, Decimal("0.05")
    )
    assert eq.key == "NSE:INFY"
    assert eq.ref == InstrumentRef(Exchange.NSE, "INFY")
    assert Exchange.NFO.is_derivatives
    assert not Exchange.NSE.is_derivatives


def test_timestamps_must_be_aware() -> None:
    ref = InstrumentRef(Exchange.NSE, "INFY")
    with pytest.raises(ValueError, match="timezone"):
        Quote(ref, Decimal(1), "kite", DataQuality.OFFICIAL_REALTIME, exchange_ts=NAIVE)
    with pytest.raises(ValueError, match="timezone"):
        Candle(NAIVE, Decimal(1), Decimal(1), Decimal(1), Decimal(1), 0)


def test_interval_properties() -> None:
    assert Interval.MINUTE_5.duration == timedelta(minutes=5)
    assert Interval.DAY_1.duration == timedelta(days=1)
    assert Interval.HOUR_1.is_intraday
    assert not Interval.DAY_1.is_intraday


# --- parsing -----------------------------------------------------------------------------


def test_parse_helpers() -> None:
    assert p.dec(1412.95) == Decimal("1412.95")
    assert p.dec("") is None
    assert p.dec(None) is None
    assert p.dec(True) is None
    assert p.dec("abc") is None
    assert p.dec("NaN") is None
    assert p.dec("inf") is None
    assert p.integer("75") == 75
    assert p.integer(1.5) is None
    assert p.integer(None) is None
    assert p.positive_or_none(Decimal(0)) is None
    assert p.positive_or_none(Decimal(1)) == 1
    with pytest.raises(BadResponse, match="last_price"):
        p.req_dec("kite", None, "last_price")
    assert p.iso_ts("2017-12-15T09:15:00+0530") == datetime(2017, 12, 15, 9, 15, tzinfo=IST)
    assert p.iso_ts("2021-06-08 15:45:56") == datetime(2021, 6, 8, 15, 45, 56, tzinfo=IST)
    assert p.iso_ts("2023-10-19T09:36:54.467+05:30") is not None
    assert p.iso_ts("nope") is None
    assert p.iso_ts(None) is None
    assert p.iso_ts("") is None
    assert p.epoch_ms("1697688414180") == datetime.fromtimestamp(1697688414.18, tz=IST)
    assert p.epoch_ms(0) is None
    assert p.iso_date("2026-10-27") == date(2026, 10, 27)
    assert p.iso_date("27-10-2026") is None
    assert p.iso_date(5) is None


@given(st.decimals(allow_nan=False, allow_infinity=False, places=2))
def test_dec_round_trips_exact_decimals(value: Decimal) -> None:
    assert p.dec(str(value)) == value


# From 1990: India's 1940s wartime offsets make some older wall-clock times ambiguous.
# Hypothesis wants naive bounds when ``timezones`` is given.
@given(st.datetimes(min_value=datetime(1990, 1, 1), timezones=st.just(IST)))  # noqa: DTZ001
def test_iso_ts_round_trips(ts: datetime) -> None:
    assert p.iso_ts(ts.isoformat()) == ts


# --- transport ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_throttle_is_per_key() -> None:
    clock = FakeClock()
    t = Throttle(clock=clock, sleep=clock.sleep)
    t.wait("kite:cred-1:quote", 1.0)
    t.wait("kite:cred-1:quote", 1.0)
    t.wait("kite:cred-2:quote", 1.0)  # another user's credential: no wait
    t.wait("kite:cred-1:quote", 0)  # no interval: no wait
    assert clock.slept == [1.0]


def transport(handler: httpx.MockTransport, **kw: object) -> Transport:
    return Transport(
        "kite",
        "https://api.example.test",
        client=httpx.Client(transport=handler),
        sleep=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


def test_transport_throttles_per_credential() -> None:
    clock = FakeClock()
    t = Transport(
        "kite",
        "https://api.example.test",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
        throttle=Throttle(clock=clock, sleep=clock.sleep),
    )
    for cred in ("a", "a", "b"):
        t.request("GET", "/q", session=make_session(cred), bucket="quote", min_interval_s=1.0)
    assert clock.slept == [1.0]


def test_transport_retries_5xx_then_succeeds() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(502 if len(calls) < 3 else 200, json={"ok": True})

    assert transport(httpx.MockTransport(handler)).json("GET", "/x", session=None) == {"ok": True}
    assert len(calls) == 3


def test_transport_gives_up_after_retries() -> None:
    t = transport(httpx.MockTransport(lambda r: httpx.Response(503)), retries=1)
    with pytest.raises(ProviderUnavailable, match="HTTP 503"):
        t.request("GET", "/x", session=None)


def test_transport_does_not_retry_when_asked_not_to() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(500)

    with pytest.raises(ProviderUnavailable):
        transport(httpx.MockTransport(handler)).request("POST", "/login", session=None, retry=False)
    assert len(calls) == 1


def test_transport_maps_status_codes() -> None:
    t429 = transport(
        httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "2"}))
    )
    with pytest.raises(RateLimited) as exc:
        t429.request("GET", "/x", session=None)
    assert exc.value.retry_after_s == 2.0

    for headers, expected in (({"Retry-After": "soon"}, None), ({}, None)):
        t = transport(httpx.MockTransport(lambda r, h=headers: httpx.Response(429, headers=h)))
        with pytest.raises(RateLimited) as exc:
            t.request("GET", "/x", session=None)
        assert exc.value.retry_after_s is expected

    with pytest.raises(SessionExpired):
        transport(httpx.MockTransport(lambda r: httpx.Response(403))).request(
            "GET", "/x", session=None
        )
    with pytest.raises(BadResponse, match="HTTP 400"):
        transport(httpx.MockTransport(lambda r: httpx.Response(400))).request(
            "GET", "/x", session=None
        )
    with pytest.raises(BadResponse, match="not JSON"):
        transport(httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))).json(
            "GET", "/x", session=None
        )


def test_transport_uses_error_mapper_first() -> None:
    def mapper(resp: httpx.Response) -> BadResponse | None:
        return BadResponse("kite", "mapped") if resp.status_code == 400 else None

    t = transport(httpx.MockTransport(lambda r: httpx.Response(400)), error_mapper=mapper)
    with pytest.raises(BadResponse, match="mapped"):
        t.request("GET", "/x", session=None)
    t2 = transport(httpx.MockTransport(lambda r: httpx.Response(401)), error_mapper=mapper)
    with pytest.raises(SessionExpired):
        t2.request("GET", "/x", session=None)


def test_network_errors_do_not_leak_request_details() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom " + req.headers.get("authorization", ""), request=req)

    t = transport(httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable) as exc:
        t.request("GET", "/x", session=None, headers={"Authorization": f"token {SECRET}"})
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__
    t.close()


def test_provider_message_is_trimmed() -> None:
    assert provider_message("  a \n b ") == "a b"
    assert len(provider_message("x" * 500)) == 200
