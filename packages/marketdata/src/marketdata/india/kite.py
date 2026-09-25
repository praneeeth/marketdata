"""Zerodha Kite Connect v3 adapter (read-only, REST over httpx, no SDK).

Shapes follow the public Kite Connect v3 docs (https://kite.trade/docs/connect/v3/).
Everything marked (verify) could not be checked against live traffic from the sandbox.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from marketdata.india import _parse as p
from marketdata.india.errors import (
    BadResponse,
    InstrumentNotResolved,
    InvalidCredentials,
    NotSupported,
    ProviderError,
    SessionExpired,
)
from marketdata.india.instrument_cache import InstrumentCache
from marketdata.india.options import batched, build_chain, select_contracts
from marketdata.india.session import ProviderSession, Secret
from marketdata.india.transport import Transport, provider_message
from marketdata.india.types import (
    IST,
    Candle,
    Capability,
    CorporateAction,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentRef,
    InstrumentType,
    Interval,
    OptionChain,
    Quote,
)

NAME = "kite"
API_BASE = "https://api.kite.trade"
LOGIN_BASE = "https://kite.zerodha.com/connect/login"

QUOTE_BATCH = 500  # instruments per /quote call (verify)
# Rate limits (verify): quote 1 req/s, historical 3 req/s, others 10 req/s.
_QUOTE_INTERVAL_S = 1.0
_HISTORICAL_INTERVAL_S = 1 / 3
_DEFAULT_INTERVAL_S = 0.1

_INTERVALS: Mapping[Interval, str] = {
    Interval.MINUTE_1: "minute",
    Interval.MINUTE_3: "3minute",
    Interval.MINUTE_5: "5minute",
    Interval.MINUTE_10: "10minute",
    Interval.MINUTE_15: "15minute",
    Interval.MINUTE_30: "30minute",
    Interval.HOUR_1: "60minute",
    Interval.DAY_1: "day",
}
# Longest date range one historical request may span, per interval (verify).
_MAX_SPAN: Mapping[Interval, timedelta] = {
    Interval.MINUTE_1: timedelta(days=60),
    Interval.MINUTE_3: timedelta(days=100),
    Interval.MINUTE_5: timedelta(days=100),
    Interval.MINUTE_10: timedelta(days=100),
    Interval.MINUTE_15: timedelta(days=200),
    Interval.MINUTE_30: timedelta(days=200),
    Interval.HOUR_1: timedelta(days=400),
    Interval.DAY_1: timedelta(days=2000),
}
_TYPES: Mapping[str, InstrumentType] = {
    "EQ": InstrumentType.EQUITY,
    "FUT": InstrumentType.FUTURE,
    "CE": InstrumentType.CALL,
    "PE": InstrumentType.PUT,
}
# Kite access tokens expire at 06:00 IST the next morning (verify).
_TOKEN_EXPIRY = time(6, 0)


def login_url(api_key: Secret, state: str = "") -> str:
    """URL the user opens to log in; Kite redirects back with ``request_token``."""
    params = {"v": "3", "api_key": api_key.reveal()}
    if state:
        params["redirect_params"] = urlencode({"state": state})
    return f"{LOGIN_BASE}?{urlencode(params)}"


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def token_expiry(issued_at: datetime) -> datetime:
    local = issued_at.astimezone(IST)
    expiry = datetime.combine(local.date(), _TOKEN_EXPIRY, tzinfo=IST)
    return expiry if local < expiry else expiry + timedelta(days=1)


def _error_mapper(resp: httpx.Response) -> ProviderError | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error_type = str(body.get("error_type", ""))
    message = provider_message(str(body.get("message", "")))
    if error_type == "TokenException":
        return SessionExpired(NAME, f"session expired or invalid: {message}")
    if error_type in ("InputException", "PermissionException", "DataException"):
        return BadResponse(NAME, f"{error_type}: {message}")
    return None


class KiteProvider:
    name = NAME
    capabilities = frozenset(
        {
            Capability.QUOTES,
            Capability.OHLC,
            Capability.INTRADAY,
            Capability.INSTRUMENTS,
            Capability.OPTION_CHAIN,
        }
    )
    quality = DataQuality.OFFICIAL_REALTIME

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        instrument_cache: InstrumentCache | None = None,
    ) -> None:
        self._t = transport or Transport(NAME, API_BASE, error_mapper=_error_mapper)
        # Shared with IndiaMarketData so option chains reuse the per-credential master.
        self._instrument_cache = instrument_cache

    # --- login -----------------------------------------------------------------------

    def exchange_request_token(
        self,
        *,
        credential_id: str,
        user_id: str,
        api_key: Secret,
        api_secret: Secret,
        request_token: str,
        now: datetime,
    ) -> ProviderSession:
        """Swap the ``request_token`` from the login redirect for an access token."""
        try:
            body = self._t.json(
                "POST",
                "/session/token",
                session=None,
                retry=False,
                headers={"X-Kite-Version": "3"},
                data={
                    "api_key": api_key.reveal(),
                    "request_token": request_token,
                    "checksum": checksum(api_key.reveal(), request_token, api_secret.reveal()),
                },
            )
        except (SessionExpired, BadResponse) as e:
            raise InvalidCredentials(NAME, "login rejected; log in to Kite again") from e
        token = _data(body).get("access_token")
        if not isinstance(token, str) or not token:
            raise BadResponse(NAME, "login response had no access_token")
        return ProviderSession(
            provider=NAME,
            credential_id=credential_id,
            user_id=user_id,
            api_key=api_key,
            access_token=Secret(token),
            expires_at=token_expiry(now),
        )

    # --- data ------------------------------------------------------------------------

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        out: list[Quote] = []
        for chunk in batched(refs, QUOTE_BATCH):
            by_key = {r.display: r for r in chunk}
            body = self._get(
                session,
                "/quote",
                params=[("i", key) for key in by_key],
                bucket="quote",
                interval=_QUOTE_INTERVAL_S,
            )
            data = _data(body)
            for key, ref in by_key.items():
                row = data.get(key)
                if isinstance(row, dict):
                    out.append(self._quote(ref, row))
        return out

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        token = ref.id_for(NAME)
        if not token:
            raise InstrumentNotResolved(NAME, f"no instrument_token for {ref.display}")
        start, end = p.aware(start).astimezone(IST), p.aware(end).astimezone(IST)
        candles: list[Candle] = []
        span = _MAX_SPAN[interval]
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + span, end)
            body = self._get(
                session,
                f"/instruments/historical/{token}/{_INTERVALS[interval]}",
                params=[
                    ("from", cursor.strftime("%Y-%m-%d %H:%M:%S")),
                    ("to", chunk_end.strftime("%Y-%m-%d %H:%M:%S")),
                    ("oi", "1"),
                ],
                bucket="historical",
                interval=_HISTORICAL_INTERVAL_S,
            )
            rows = _data(body).get("candles")
            if not isinstance(rows, list):
                raise BadResponse(NAME, "historical response had no candles")
            candles.extend(c for c in (_candle(r) for r in rows) if c is not None)
            cursor = chunk_end + timedelta(seconds=1)
        seen: dict[datetime, Candle] = {c.ts: c for c in candles if start <= c.ts <= end}
        return [seen[ts] for ts in sorted(seen)]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        resp = self._t.request(
            "GET",
            f"/instruments/{exchange.value}",
            session=session,
            bucket="default",
            min_interval_s=_DEFAULT_INTERVAL_S,
            headers=self._headers(session),
        )
        for row in csv.DictReader(io.StringIO(resp.text)):
            inst = _instrument(row)
            if inst is not None:
                yield inst

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        raise NotSupported(NAME, "Kite Connect has no corporate actions API")

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        fo = Exchange.BFO if underlying.exchange is Exchange.BSE else Exchange.NFO
        contracts = select_contracts(self._cached_instruments(session, fo), underlying, expiry)
        spot_quotes = self.quotes(session, [underlying])
        return build_chain(
            underlying=underlying,
            expiry=expiry,
            contracts=contracts,
            fetch_quotes=lambda refs: self.quotes(session, refs),
            spot=spot_quotes[0].last_price if spot_quotes else None,
            source=NAME,
            quality=self.quality,
        )

    # --- helpers ---------------------------------------------------------------------

    def _cached_instruments(
        self, session: ProviderSession, exchange: Exchange
    ) -> Iterable[Instrument]:
        if self._instrument_cache is None:
            return self.instruments(session, exchange)
        return self._instrument_cache.get(
            session, exchange, lambda: self.instruments(session, exchange)
        ).values()

    def _headers(self, session: ProviderSession) -> dict[str, str]:
        if not session.access_token:
            raise SessionExpired(NAME, "not logged in")
        return {
            "X-Kite-Version": "3",
            "Authorization": (f"token {session.api_key.reveal()}:{session.access_token.reveal()}"),
        }

    def _get(
        self,
        session: ProviderSession,
        path: str,
        *,
        params: list[tuple[str, str]],
        bucket: str,
        interval: float,
    ) -> Any:
        return self._t.json(
            "GET",
            path,
            session=session,
            bucket=bucket,
            min_interval_s=interval,
            params=httpx.QueryParams(tuple(params)),
            headers=self._headers(session),
        )

    def _quote(self, ref: InstrumentRef, row: Mapping[str, Any]) -> Quote:
        last = p.req_dec(NAME, row.get("last_price"), "last_price")
        raw_ohlc = row.get("ohlc")
        ohlc: Mapping[str, Any] = raw_ohlc if isinstance(raw_ohlc, dict) else {}
        prev = p.positive_or_none(p.dec(ohlc.get("close")))
        change = last - prev if prev is not None else None
        token = row.get("instrument_token")
        if token is not None and not ref.id_for(NAME):
            ref = ref.with_id(NAME, str(token))
        return Quote(
            instrument=ref,
            last_price=last,
            source=NAME,
            quality=self.quality,
            open=p.positive_or_none(p.dec(ohlc.get("open"))),
            high=p.positive_or_none(p.dec(ohlc.get("high"))),
            low=p.positive_or_none(p.dec(ohlc.get("low"))),
            prev_close=prev,
            change=change,
            change_pct=p.pct_change(change, prev),
            volume=p.integer(row.get("volume")),
            average_price=p.positive_or_none(p.dec(row.get("average_price"))),
            oi=p.integer(row.get("oi")),
            upper_circuit=p.positive_or_none(p.dec(row.get("upper_circuit_limit"))),
            lower_circuit=p.positive_or_none(p.dec(row.get("lower_circuit_limit"))),
            exchange_ts=p.iso_ts(row.get("timestamp")),
            last_trade_ts=p.iso_ts(row.get("last_trade_time")),
        )


def _data(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("status") != "success":
        raise BadResponse(NAME, "unexpected response envelope")
    data = body.get("data")
    if not isinstance(data, dict):
        raise BadResponse(NAME, "response had no data object")
    return data


def _candle(row: Any) -> Candle | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    ts = p.iso_ts(row[0])
    values = [p.dec(v) for v in row[1:5]]
    volume = p.integer(row[5])
    if ts is None or volume is None or any(v is None for v in values):
        return None
    o, h, low, c = (v for v in values if v is not None)
    return Candle(ts, o, h, low, c, volume, p.integer(row[6]) if len(row) > 6 else None)


def _instrument(row: Mapping[str, str]) -> Instrument | None:
    try:
        exchange = Exchange(row.get("exchange", ""))
    except ValueError:
        return None  # MCX, CDS, etc. are out of scope
    symbol = row.get("tradingsymbol", "")
    token = row.get("instrument_token", "")
    tick = p.dec(row.get("tick_size"))
    lot = p.integer(row.get("lot_size"))
    if not symbol or not token or tick is None or lot is None:
        return None
    segment = row.get("segment", "")
    itype = (
        InstrumentType.INDEX
        if segment == "INDICES"
        else _TYPES.get(row.get("instrument_type", ""), InstrumentType.OTHER)
    )
    derivative = itype in (InstrumentType.FUTURE, InstrumentType.CALL, InstrumentType.PUT)
    name = row.get("name", "") or symbol
    return Instrument(
        exchange=exchange,
        tradingsymbol=symbol,
        name=name,
        instrument_type=itype,
        segment=segment,
        lot_size=lot,
        tick_size=tick,
        expiry=p.iso_date(row.get("expiry")),
        strike=p.positive_or_none(p.dec(row.get("strike"))),
        underlying=name if derivative else None,
        provider_ids=((NAME, token),),
    )
