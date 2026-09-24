"""Angel One SmartAPI adapter (read-only, REST over httpx, no SDK).

Login takes the user's client code, PIN and a TOTP they type in at that moment. TOTP
seeds are never stored (decision Q10). Shapes follow https://smartapi.angelbroking.com/docs.
Everything marked (verify) could not be checked against live traffic from the sandbox.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx

from marketdata.india import _parse as p
from marketdata.india.errors import (
    BadResponse,
    InstrumentNotResolved,
    InvalidCredentials,
    NotSupported,
    ProviderError,
    RateLimited,
    SessionExpired,
)
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

NAME = "angel"
API_BASE = "https://apiconnect.angelone.in"
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

QUOTE_BATCH = 50  # tokens per quote call (verify)
_QUOTE_INTERVAL_S = 1.0  # (verify)
_HISTORICAL_INTERVAL_S = 1 / 3  # (verify)
_TOKEN_ERRORS = frozenset({"AG8001", "AG8002", "AG8003"})  # invalid / expired / missing (verify)
# SmartAPI sessions end at midnight IST (verify).
_TOKEN_EXPIRY = time(0, 0)

_INTERVALS: Mapping[Interval, tuple[str, timedelta]] = {
    Interval.MINUTE_1: ("ONE_MINUTE", timedelta(days=30)),
    Interval.MINUTE_3: ("THREE_MINUTE", timedelta(days=60)),
    Interval.MINUTE_5: ("FIVE_MINUTE", timedelta(days=100)),
    Interval.MINUTE_10: ("TEN_MINUTE", timedelta(days=100)),
    Interval.MINUTE_15: ("FIFTEEN_MINUTE", timedelta(days=200)),
    Interval.MINUTE_30: ("THIRTY_MINUTE", timedelta(days=200)),
    Interval.HOUR_1: ("ONE_HOUR", timedelta(days=400)),
    Interval.DAY_1: ("ONE_DAY", timedelta(days=2000)),
}
_DERIVATIVE_TYPES = frozenset({"OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"})
# SmartAPI requires these client headers; the values are not used for authorisation of
# market-data calls (verify: static-IP rules apply to order APIs, which we never call).
_CLIENT_HEADERS = {
    "X-UserType": "USER",
    "X-SourceID": "WEB",
    "X-ClientLocalIP": "127.0.0.1",
    "X-ClientPublicIP": "127.0.0.1",
    "X-MACAddress": "00:00:00:00:00:00",
    "Accept": "application/json",
}


def token_expiry(issued_at: datetime) -> datetime:
    local = issued_at.astimezone(IST)
    return datetime.combine(local.date() + timedelta(days=1), _TOKEN_EXPIRY, tzinfo=IST)


def _envelope_error(body: Mapping[str, Any]) -> ProviderError:
    code = str(body.get("errorcode") or "")
    message = provider_message(str(body.get("message") or ""))
    if code in _TOKEN_ERRORS:
        return SessionExpired(NAME, f"session expired or invalid: {message}")
    if "access rate" in message.lower():
        return RateLimited(NAME, message)
    return BadResponse(NAME, f"{code}: {message}" if code else message or "request failed")


def _error_mapper(resp: httpx.Response) -> ProviderError | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("status") is False:
        return _envelope_error(body)
    return None


class AngelProvider:
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

    def __init__(self, transport: Transport | None = None) -> None:
        self._t = transport or Transport(NAME, API_BASE, error_mapper=_error_mapper)

    # --- login -----------------------------------------------------------------------

    def login(
        self,
        *,
        credential_id: str,
        user_id: str,
        api_key: Secret,
        client_code: Secret,
        pin: Secret,
        totp: str,
        now: datetime,
    ) -> ProviderSession:
        """Log in with a TOTP the user has just typed. The TOTP is used once, never kept."""
        try:
            body = self._t.json(
                "POST",
                "/rest/auth/angelbroking/user/v1/loginByPassword",
                session=None,
                retry=False,
                headers={**_CLIENT_HEADERS, "X-PrivateKey": api_key.reveal()},
                json={"clientcode": client_code.reveal(), "password": pin.reveal(), "totp": totp},
            )
            data = _data(body)
        except (SessionExpired, BadResponse) as e:
            raise InvalidCredentials(NAME, "login rejected; check client code, PIN and TOTP") from e
        token = data.get("jwtToken") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise BadResponse(NAME, "login response had no jwtToken")
        return ProviderSession(
            provider=NAME,
            credential_id=credential_id,
            user_id=user_id,
            api_key=api_key,
            access_token=Secret(token),
            extra={"client_code": client_code},
            expires_at=token_expiry(now),
        )

    # --- data ------------------------------------------------------------------------

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        out: list[Quote] = []
        for chunk in batched(refs, QUOTE_BATCH):
            by_token: dict[tuple[str, str], InstrumentRef] = {}
            tokens: dict[str, list[str]] = {}
            for ref in chunk:
                token = _token(ref)
                by_token[(ref.exchange.value, token)] = ref
                tokens.setdefault(ref.exchange.value, []).append(token)
            data = _data(
                self._post(
                    session,
                    "/rest/secure/angelbroking/market/v1/quote/",
                    {"mode": "FULL", "exchangeTokens": tokens},
                    bucket="quote",
                    interval=_QUOTE_INTERVAL_S,
                )
            )
            fetched = data.get("fetched") if isinstance(data, dict) else None
            if not isinstance(fetched, list):
                raise BadResponse(NAME, "quote response had no fetched list")
            for row in fetched:
                if not isinstance(row, dict):
                    continue
                key = (str(row.get("exchange", "")), str(row.get("symbolToken", "")))
                matched = by_token.get(key)
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
        token = _token(ref)
        start, end = p.aware(start).astimezone(IST), p.aware(end).astimezone(IST)
        name, span = _INTERVALS[interval]
        rows: list[Any] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + span, end)
            data = _data(
                self._post(
                    session,
                    "/rest/secure/angelbroking/historical/v1/getCandleData",
                    {
                        "exchange": ref.exchange.value,
                        "symboltoken": token,
                        "interval": name,
                        "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                        "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
                    },
                    bucket="historical",
                    interval=_HISTORICAL_INTERVAL_S,
                )
            )
            if not isinstance(data, list):
                raise BadResponse(NAME, "historical response had no candle list")
            rows.extend(data)
            cursor = chunk_end + timedelta(minutes=1)
        seen = {c.ts: c for c in (_candle(r) for r in rows) if c and start <= c.ts <= end}
        return [seen[ts] for ts in sorted(seen)]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        body = self._t.json("GET", SCRIP_MASTER_URL, session=session, bucket="instruments")
        if not isinstance(body, list):
            raise BadResponse(NAME, "scrip master was not a list")
        for row in body:
            inst = _instrument(row) if isinstance(row, dict) else None
            if inst is not None and inst.exchange is exchange:
                yield inst

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        raise NotSupported(NAME, "SmartAPI has no corporate actions API")

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        fo = Exchange.BFO if underlying.exchange is Exchange.BSE else Exchange.NFO
        contracts = select_contracts(self.instruments(session, fo), underlying, expiry)
        spot = self.quotes(session, [underlying]) if underlying.id_for(NAME) else []
        return build_chain(
            underlying=underlying,
            expiry=expiry,
            contracts=contracts,
            fetch_quotes=lambda refs: self.quotes(session, refs),
            spot=spot[0].last_price if spot else None,
            source=NAME,
            quality=self.quality,
        )

    # --- helpers ---------------------------------------------------------------------

    def _post(
        self,
        session: ProviderSession,
        path: str,
        payload: Mapping[str, Any],
        *,
        bucket: str,
        interval: float,
    ) -> Any:
        if not session.access_token:
            raise SessionExpired(NAME, "not logged in")
        return self._t.json(
            "POST",
            path,
            session=session,
            bucket=bucket,
            min_interval_s=interval,
            json=payload,
            headers={
                **_CLIENT_HEADERS,
                "X-PrivateKey": session.api_key.reveal(),
                "Authorization": f"Bearer {session.access_token.reveal()}",
            },
        )

    def _quote(self, ref: InstrumentRef, row: Mapping[str, Any]) -> Quote:
        last = p.req_dec(NAME, row.get("ltp"), "ltp")
        prev = p.positive_or_none(p.dec(row.get("close")))
        change = p.dec(row.get("netChange"))
        return Quote(
            instrument=ref,
            last_price=last,
            source=NAME,
            quality=self.quality,
            open=p.positive_or_none(p.dec(row.get("open"))),
            high=p.positive_or_none(p.dec(row.get("high"))),
            low=p.positive_or_none(p.dec(row.get("low"))),
            prev_close=prev,
            change=change,
            change_pct=p.pct_change(change, prev),
            volume=p.integer(row.get("tradeVolume")),
            average_price=p.positive_or_none(p.dec(row.get("avgPrice"))),
            oi=p.integer(row.get("opnInterest")),
            upper_circuit=p.positive_or_none(p.dec(row.get("upperCircuit"))),
            lower_circuit=p.positive_or_none(p.dec(row.get("lowerCircuit"))),
            exchange_ts=_angel_ts(row.get("exchFeedTime")),
            last_trade_ts=_angel_ts(row.get("exchTradeTime")),
        )


def _token(ref: InstrumentRef) -> str:
    token = ref.id_for(NAME)
    if not token:
        raise InstrumentNotResolved(NAME, f"no symboltoken for {ref.display}")
    return token


def _data(body: Any) -> Any:
    if not isinstance(body, dict):
        raise BadResponse(NAME, "unexpected response envelope")
    if body.get("status") is not True:
        raise _envelope_error(body)
    return body.get("data")


def _angel_ts(value: Any) -> datetime | None:
    """SmartAPI quote times look like ``21-Dec-2022 10:31:10`` (IST)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def _candle(row: Any) -> Candle | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    ts = p.iso_ts(row[0])
    o, h, low, c = (p.dec(v) for v in row[1:5])
    volume = p.integer(row[5])
    if ts is None or volume is None or o is None or h is None or low is None or c is None:
        return None
    return Candle(ts, o, h, low, c, volume)


def _instrument(row: Mapping[str, Any]) -> Instrument | None:
    try:
        exchange = Exchange(str(row.get("exch_seg", "")))
    except ValueError:
        return None
    token = str(row.get("token") or "")
    symbol = str(row.get("symbol") or "")
    name = str(row.get("name") or "")
    lot = p.integer(p.dec(row.get("lotsize")))
    tick_paise = p.dec(row.get("tick_size"))
    if not token or not symbol or lot is None or lot < 1 or tick_paise is None:
        return None
    raw_type = str(row.get("instrumenttype") or "")
    if raw_type == "AMXIDX":
        itype, symbol = InstrumentType.INDEX, symbol.upper()
    elif raw_type in ("OPTIDX", "OPTSTK"):
        if symbol.endswith("CE"):
            itype = InstrumentType.CALL
        elif symbol.endswith("PE"):
            itype = InstrumentType.PUT
        else:
            return None
    elif raw_type in ("FUTIDX", "FUTSTK"):
        itype = InstrumentType.FUTURE
    elif raw_type == "" and symbol.endswith("-EQ"):
        itype, symbol = InstrumentType.EQUITY, symbol.removesuffix("-EQ")
    elif raw_type == "":
        itype = InstrumentType.EQUITY  # other series (BE, SM, ...) keep their suffix
    else:
        itype = InstrumentType.OTHER
    strike_paise = p.positive_or_none(p.dec(row.get("strike")))
    return Instrument(
        exchange=exchange,
        tradingsymbol=symbol,
        name=name or symbol,
        instrument_type=itype,
        segment=raw_type or exchange.value,
        lot_size=lot,
        tick_size=tick_paise / 100,  # SmartAPI publishes tick size in paise (verify)
        expiry=_expiry(row.get("expiry")),
        strike=strike_paise / 100 if strike_paise is not None else None,  # paise (verify)
        underlying=name if raw_type in _DERIVATIVE_TYPES else None,
        provider_ids=((NAME, token),),
    )


def _expiry(value: Any) -> date | None:
    """Scrip-master expiries look like ``27OCT2026``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value.title(), "%d%b%Y").replace(tzinfo=IST).date()
    except ValueError:
        return None
