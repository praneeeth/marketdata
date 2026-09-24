"""Typed market data for Indian exchanges.

Prices are ``Decimal`` so tick sizes, strikes and INR amounts stay exact. Every timestamp
is timezone-aware (``Asia/Kolkata`` unless the provider says otherwise).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Exchange(enum.StrEnum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"  # NSE futures & options
    BFO = "BFO"  # BSE futures & options

    @property
    def is_derivatives(self) -> bool:
        return self in (Exchange.NFO, Exchange.BFO)


class InstrumentType(enum.StrEnum):
    EQUITY = "EQ"
    INDEX = "INDEX"
    FUTURE = "FUT"
    CALL = "CE"
    PUT = "PE"
    OTHER = "OTHER"


class Capability(enum.StrEnum):
    QUOTES = "quotes"
    OHLC = "ohlc"
    INTRADAY = "intraday"
    INSTRUMENTS = "instruments"
    CORP_ACTIONS = "corporate_actions"
    OPTION_CHAIN = "option_chain"


class DataQuality(enum.StrEnum):
    OFFICIAL_REALTIME = "official_realtime"
    OFFICIAL_DELAYED = "official_delayed"
    UNOFFICIAL_DELAYED = "unofficial_delayed"


class Interval(enum.StrEnum):
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_10 = "10m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "60m"
    DAY_1 = "1d"

    @property
    def duration(self) -> timedelta:
        return _INTERVAL_DURATION[self]

    @property
    def is_intraday(self) -> bool:
        return self is not Interval.DAY_1


_INTERVAL_DURATION: Mapping[Interval, timedelta] = {
    Interval.MINUTE_1: timedelta(minutes=1),
    Interval.MINUTE_3: timedelta(minutes=3),
    Interval.MINUTE_5: timedelta(minutes=5),
    Interval.MINUTE_10: timedelta(minutes=10),
    Interval.MINUTE_15: timedelta(minutes=15),
    Interval.MINUTE_30: timedelta(minutes=30),
    Interval.HOUR_1: timedelta(hours=1),
    Interval.DAY_1: timedelta(days=1),
}


@dataclass(frozen=True)
class InstrumentRef:
    """Identifies an instrument, plus each provider's own ID for it where known.

    Provider IDs come from that provider's instrument master: Kite ``instrument_token``,
    Upstox ``instrument_key``, Angel ``symboltoken``, yfinance ticker. A ref without the
    calling provider's ID must be resolved through the instrument master first.
    """

    exchange: Exchange
    tradingsymbol: str
    provider_ids: tuple[tuple[str, str], ...] = ()

    def id_for(self, provider: str) -> str | None:
        for name, value in self.provider_ids:
            if name == provider:
                return value
        return None

    def with_id(self, provider: str, value: str) -> InstrumentRef:
        ids = tuple((n, v) for n, v in self.provider_ids if n != provider)
        return InstrumentRef(self.exchange, self.tradingsymbol, (*ids, (provider, value)))

    @property
    def display(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"


@dataclass(frozen=True)
class Instrument:
    exchange: Exchange
    tradingsymbol: str
    name: str
    instrument_type: InstrumentType
    segment: str
    lot_size: int
    tick_size: Decimal
    isin: str | None = None
    expiry: date | None = None
    strike: Decimal | None = None
    underlying: str | None = None  # e.g. "NIFTY" for NIFTY options/futures
    provider_ids: tuple[tuple[str, str], ...] = ()

    @property
    def ref(self) -> InstrumentRef:
        return InstrumentRef(self.exchange, self.tradingsymbol, self.provider_ids)

    @property
    def key(self) -> str:
        """Provider-independent identity.

        Brokers spell derivative symbols differently (``NIFTY24FEB22000CE`` vs
        ``NIFTY 22000 CE 29 FEB 24``), so derivatives are keyed by their contract terms.
        """
        if self.instrument_type in (InstrumentType.FUTURE, InstrumentType.CALL, InstrumentType.PUT):
            strike = "" if self.strike is None else format(self.strike.normalize(), "f")
            expiry = "" if self.expiry is None else self.expiry.isoformat()
            return f"{self.exchange}:{self.underlying}:{expiry}:{strike}:{self.instrument_type}"
        return f"{self.exchange}:{self.tradingsymbol}"


@dataclass(frozen=True)
class Quote:
    instrument: InstrumentRef
    last_price: Decimal
    source: str
    quality: DataQuality
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    prev_close: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    volume: int | None = None
    average_price: Decimal | None = None
    oi: int | None = None
    upper_circuit: Decimal | None = None
    lower_circuit: Decimal | None = None
    exchange_ts: datetime | None = None
    last_trade_ts: datetime | None = None

    def __post_init__(self) -> None:
        for ts in (self.exchange_ts, self.last_trade_ts):
            if ts is not None and ts.tzinfo is None:
                raise ValueError("Quote timestamps must be timezone-aware")


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int | None = None

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Candle.ts must be timezone-aware")


class CorporateActionKind(enum.StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"
    BONUS = "bonus"
    OTHER = "other"


@dataclass(frozen=True)
class CorporateAction:
    instrument: InstrumentRef
    kind: CorporateActionKind
    ex_date: date
    source: str
    quality: DataQuality
    amount: Decimal | None = None  # dividend per share, INR
    ratio: Decimal | None = None  # split/bonus factor, new shares per old share
    description: str = ""


@dataclass(frozen=True)
class OptionQuote:
    instrument: InstrumentRef
    last_price: Decimal | None = None
    oi: int | None = None
    volume: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None


@dataclass(frozen=True)
class OptionChainRow:
    strike: Decimal
    call: OptionQuote | None = None
    put: OptionQuote | None = None


@dataclass(frozen=True)
class OptionChain:
    underlying: InstrumentRef
    expiry: date
    source: str
    quality: DataQuality
    spot: Decimal | None = None
    rows: tuple[OptionChainRow, ...] = field(default_factory=tuple)
