"""Upstox API adapter (read-only, REST over httpx, no SDK).

Uses the v2 login, market-quote and option-chain endpoints and the v3 historical candle
endpoints, following https://upstox.com/developer/api-documentation/. Everything marked
(verify) could not be checked against live traffic from the sandbox.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

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
    OptionChainRow,
    OptionQuote,
    Quote,
)

NAME = "upstox"
API_BASE = "https://api.upstox.com"
INSTRUMENTS_BASE = "https://assets.upstox.com/market-quote/instruments/exchange"

QUOTE_BATCH = 500  # instrument keys per quote call (verify)
_QUOTE_INTERVAL_S = 0.1  # (verify) documented limits are per second/minute/30 minutes
_HISTORICAL_INTERVAL_S = 0.1
_EXPIRED_TOKEN_CODES = frozenset({"UDAPI100050", "UDAPI100016"})  # (verify)
# Upstox access tokens expire at 03:30 IST the next day (verify).
_TOKEN_EXPIRY = time(3, 30)

# v3 historical: (unit, interval, longest range per request) (verify).
_V3: Mapping[Interval, tuple[str, int, timedelta]] = {
    Interval.MINUTE_1: ("minutes", 1, timedelta(days=28)),
    Interval.MINUTE_3: ("minutes", 3, timedelta(days=28)),
    Interval.MINUTE_5: ("minutes", 5, timedelta(days=28)),
    Interval.MINUTE_10: ("minutes", 10, timedelta(days=28)),
    Interval.MINUTE_15: ("minutes", 15, timedelta(days=28)),
    Interval.MINUTE_30: ("minutes", 30, timedelta(days=89)),
    Interval.HOUR_1: ("hours", 1, timedelta(days=89)),
    Interval.DAY_1: ("days", 1, timedelta(days=3650)),
}
_SEGMENTS: Mapping[str, Exchange] = {
    "NSE_EQ": Exchange.NSE,
    "NSE_INDEX": Exchange.NSE,
    "NSE_FO": Exchange.NFO,
    "BSE_EQ": Exchange.BSE,
    "BSE_INDEX": Exchange.BSE,
    "BSE_FO": Exchange.BFO,
}
_FILES: Mapping[Exchange, str] = {
    Exchange.NSE: "NSE",
    Exchange.NFO: "NSE",
    Exchange.BSE: "BSE",
    Exchange.BFO: "BSE",
}
_TYPES: Mapping[str, InstrumentType] = {
    "EQ": InstrumentType.EQUITY,
    "INDEX": InstrumentType.INDEX,
    "FUT": InstrumentType.FUTURE,
    "CE": InstrumentType.CALL,
    "PE": InstrumentType.PUT,
}


def authorize_url(client_id: Secret, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id.reveal(),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{API_BASE}/v2/login/authorization/dialog?{urlencode(params)}"


def token_expiry(issued_at: datetime) -> datetime:
    local = issued_at.astimezone(IST)
    expiry = datetime.combine(local.date(), _TOKEN_EXPIRY, tzinfo=IST)
    return expiry if local < expiry else expiry + timedelta(days=1)


def _error_mapper(resp: httpx.Response) -> ProviderError | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return None
    first = errors[0]
    code = str(first.get("errorCode") or first.get("error_code") or "")
    message = provider_message(str(first.get("message", "")))
    if code in _EXPIRED_TOKEN_CODES or resp.status_code == 401:
        return SessionExpired(NAME, f"session expired or invalid: {message}")
    return BadResponse(NAME, f"{code}: {message}" if code else message)


class UpstoxProvider:
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
        now: Callable[[], datetime] = lambda: datetime.now(IST),
    ) -> None:
        self._t = transport or Transport(NAME, API_BASE, error_mapper=_error_mapper)
        self._now = now

    # --- login -----------------------------------------------------------------------

    def exchange_code(
        self,
        *,
        credential_id: str,
        user_id: str,
        client_id: Secret,
        client_secret: Secret,
        redirect_uri: str,
        code: str,
        now: datetime,
    ) -> ProviderSession:
        try:
            body = self._t.json(
                "POST",
                "/v2/login/authorization/token",
                session=None,
                retry=False,
                headers={"Accept": "application/json"},
                data={
                    "code": code,
                    "client_id": client_id.reveal(),
                    "client_secret": client_secret.reveal(),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except (SessionExpired, BadResponse) as e:
            raise InvalidCredentials(NAME, "login rejected; log in to Upstox again") from e
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise BadResponse(NAME, "login response had no access_token")
        return ProviderSession(
            provider=NAME,
            credential_id=credential_id,
            user_id=user_id,
            api_key=client_id,
            access_token=Secret(token),
            expires_at=token_expiry(now),
        )

    # --- data ------------------------------------------------------------------------

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        by_key: dict[str, InstrumentRef] = {}
        for ref in refs:
            by_key[_key(ref)] = ref
        keys = list(by_key)
        out: list[Quote] = []
        for i in range(0, len(keys), QUOTE_BATCH):
            chunk = keys[i : i + QUOTE_BATCH]
            data = _data(
                self._get(
                    session,
                    "/v2/market-quote/quotes",
                    params={"instrument_key": ",".join(chunk)},
                    bucket="quote",
                    interval=_QUOTE_INTERVAL_S,
                )
            )
            if not isinstance(data, dict):
                raise BadResponse(NAME, "quote response had no data object")
            for row in data.values():
                if not isinstance(row, dict):
                    continue
                matched = by_key.get(str(row.get("instrument_token", "")))
                if matched is not None:
                    out.append(self._quote(matched, row))
        return out

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        key = _key(ref)
        start, end = p.aware(start).astimezone(IST), p.aware(end).astimezone(IST)
        unit, n, span = _V3[interval]
        encoded = quote(key, safe="")
        rows: list[Any] = []
        cursor = start.date()
        while cursor <= end.date():
            chunk_end = min(cursor + span, end.date())
            rows.extend(
                self._candle_rows(
                    session,
                    f"/v3/historical-candle/{encoded}/{unit}/{n}/"
                    f"{chunk_end.isoformat()}/{cursor.isoformat()}",
                )
            )
            cursor = chunk_end + timedelta(days=1)
        # The historical endpoint stops at the previous session; today comes from intraday.
        if end.date() >= self._now().astimezone(IST).date() and unit != "days":
            rows.extend(
                self._candle_rows(session, f"/v3/historical-candle/intraday/{encoded}/{unit}/{n}")
            )
        seen = {c.ts: c for c in (_candle(r) for r in rows) if c and start <= c.ts <= end}
        return [seen[ts] for ts in sorted(seen)]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        resp = self._t.request(
            "GET",
            f"{INSTRUMENTS_BASE}/{_FILES[exchange]}.json.gz",
            session=session,
            bucket="instruments",
        )
        try:
            raw = gzip.decompress(resp.content)
        except OSError:
            raw = resp.content  # already decoded by the HTTP layer
        try:
            rows = json.loads(raw)
        except ValueError:
            raise BadResponse(NAME, "instrument file was not JSON") from None
        if not isinstance(rows, list):
            raise BadResponse(NAME, "instrument file was not a list")
        for row in rows:
            inst = _instrument(row) if isinstance(row, dict) else None
            if inst is not None and inst.exchange is exchange:
                yield inst

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        raise NotSupported(NAME, "Upstox has no corporate actions API")

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        data = _data(
            self._get(
                session,
                "/v2/option/chain",
                params={"instrument_key": _key(underlying), "expiry_date": expiry.isoformat()},
                bucket="default",
                interval=_QUOTE_INTERVAL_S,
            )
        )
        if not isinstance(data, list):
            raise BadResponse(NAME, "option chain response was not a list")
        fo = Exchange.BFO if underlying.exchange is Exchange.BSE else Exchange.NFO
        rows: dict[Decimal, OptionChainRow] = {}
        spot: Decimal | None = None
        for item in data:
            if not isinstance(item, dict):
                continue
            strike = p.dec(item.get("strike_price"))
            if strike is None:
                continue
            spot = spot or p.dec(item.get("underlying_spot_price"))
            rows[strike] = OptionChainRow(
                strike,
                _option(fo, item.get("call_options")),
                _option(fo, item.get("put_options")),
            )
        return OptionChain(
            underlying=underlying,
            expiry=expiry,
            source=NAME,
            quality=self.quality,
            spot=spot,
            rows=tuple(rows[s] for s in sorted(rows)),
        )

    # --- helpers ---------------------------------------------------------------------

    def _headers(self, session: ProviderSession) -> dict[str, str]:
        if not session.access_token:
            raise SessionExpired(NAME, "not logged in")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {session.access_token.reveal()}",
        }

    def _get(
        self,
        session: ProviderSession,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        bucket: str,
        interval: float,
    ) -> Any:
        return self._t.json(
            "GET",
            path,
            session=session,
            bucket=bucket,
            min_interval_s=interval,
            params=params,
            headers=self._headers(session),
        )

    def _candle_rows(self, session: ProviderSession, path: str) -> list[Any]:
        data = _data(self._get(session, path, bucket="historical", interval=_HISTORICAL_INTERVAL_S))
        rows = data.get("candles") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise BadResponse(NAME, "historical response had no candles")
        return rows

    def _quote(self, ref: InstrumentRef, row: Mapping[str, Any]) -> Quote:
        last = p.req_dec(NAME, row.get("last_price"), "last_price")
        raw_ohlc = row.get("ohlc")
        ohlc: Mapping[str, Any] = raw_ohlc if isinstance(raw_ohlc, dict) else {}
        change = p.dec(row.get("net_change"))
        # Upstox ``ohlc.close`` is the current session's close, so derive the previous
        # close from ``net_change`` (verify).
        prev = p.positive_or_none(last - change) if change is not None else None
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
            last_trade_ts=p.epoch_ms(row.get("last_trade_time")),
        )


def _key(ref: InstrumentRef) -> str:
    key = ref.id_for(NAME)
    if not key:
        raise InstrumentNotResolved(NAME, f"no instrument_key for {ref.display}")
    return key


def _data(body: Any) -> Any:
    if not isinstance(body, dict) or body.get("status") != "success":
        raise BadResponse(NAME, "unexpected response envelope")
    return body.get("data")


def _candle(row: Any) -> Candle | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    ts = p.iso_ts(row[0])
    o, h, low, c = (p.dec(v) for v in row[1:5])
    volume = p.integer(row[5])
    if ts is None or volume is None or o is None or h is None or low is None or c is None:
        return None
    return Candle(ts, o, h, low, c, volume, p.integer(row[6]) if len(row) > 6 else None)


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _option(exchange: Exchange, raw: Any) -> OptionQuote | None:
    if not isinstance(raw, dict):
        return None
    key = raw.get("instrument_key")
    if not isinstance(key, str) or not key:
        return None
    md = _obj(raw.get("market_data"))
    greeks = _obj(raw.get("option_greeks"))
    # The chain only carries instrument keys, so the key doubles as the symbol here.
    return OptionQuote(
        instrument=InstrumentRef(exchange, key).with_id(NAME, key),
        last_price=p.dec(md.get("ltp")),
        oi=p.integer(md.get("oi")),
        volume=p.integer(md.get("volume")),
        bid=p.positive_or_none(p.dec(md.get("bid_price"))),
        ask=p.positive_or_none(p.dec(md.get("ask_price"))),
        iv=p.dec(greeks.get("iv")),
        delta=p.dec(greeks.get("delta")),
        gamma=p.dec(greeks.get("gamma")),
        theta=p.dec(greeks.get("theta")),
        vega=p.dec(greeks.get("vega")),
    )


def _instrument(row: Mapping[str, Any]) -> Instrument | None:
    exchange = _SEGMENTS.get(str(row.get("segment", "")))
    key = row.get("instrument_key")
    lot = p.integer(row.get("lot_size"))
    tick_paise = p.dec(row.get("tick_size"))
    if exchange is None or not isinstance(key, str) or not key or lot is None:
        return None
    if tick_paise is None:
        return None
    itype = _TYPES.get(str(row.get("instrument_type", "")), InstrumentType.OTHER)
    name = str(row.get("name") or "")
    symbol = str(row.get("trading_symbol") or "")
    if itype is InstrumentType.INDEX:
        # Upstox calls NIFTY 50 "NIFTY" with name "Nifty 50"; the exchange spells it
        # "NIFTY 50", which is what Kite uses, so key indices by upper-cased name.
        symbol = name.upper()
    if not symbol:
        return None
    if lot < 1:
        if itype is not InstrumentType.INDEX:
            return None
        lot = 1  # Upstox lists indices with lot 0; Kite uses 1. Not tradable either way.
    derivative = itype in (InstrumentType.FUTURE, InstrumentType.CALL, InstrumentType.PUT)
    expiry = p.epoch_ms(row.get("expiry"))
    isin = row.get("isin")
    return Instrument(
        exchange=exchange,
        tradingsymbol=symbol,
        name=name or symbol,
        instrument_type=itype,
        segment=str(row.get("segment")),
        lot_size=lot,
        tick_size=tick_paise / 100,  # Upstox publishes tick size in paise (verify)
        isin=isin if isinstance(isin, str) and isin else None,
        expiry=expiry.date() if expiry else None,
        strike=p.positive_or_none(p.dec(row.get("strike_price"))),
        underlying=str(row.get("underlying_symbol") or name) if derivative else None,
        provider_ids=((NAME, key),),
    )
