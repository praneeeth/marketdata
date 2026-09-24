"""Development-only yfinance adapter for NSE/BSE cash instruments.

Yahoo Finance data is unofficial and delayed, and its terms do not allow redistribution.
This adapter refuses to construct unless ``ALLOW_UNOFFICIAL_DATA`` is true *and*
``APP_ENV`` is explicitly a non-production environment; a missing ``APP_ENV`` counts as
production (fail closed). Every object it returns is tagged ``UNOFFICIAL_DELAYED``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from marketdata.india import _parse as p
from marketdata.india.errors import (
    InstrumentNotResolved,
    NotSupported,
    ProviderUnavailable,
    UnofficialDataDisabled,
)
from marketdata.india.session import ProviderSession
from marketdata.india.types import (
    IST,
    Candle,
    Capability,
    CorporateAction,
    CorporateActionKind,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentRef,
    Interval,
    OptionChain,
    Quote,
)

NAME = "yfinance"
_TRUE = frozenset({"1", "true", "yes", "on"})
_NON_PRODUCTION = frozenset({"development", "dev", "local", "test"})
_INTERVALS: Mapping[Interval, str] = {
    Interval.MINUTE_1: "1m",
    Interval.MINUTE_5: "5m",
    Interval.MINUTE_15: "15m",
    Interval.MINUTE_30: "30m",
    Interval.HOUR_1: "60m",
    Interval.DAY_1: "1d",
}
# Yahoo index tickers (verify before adding more).
_INDEX_TICKERS: Mapping[str, str] = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "SENSEX": "^BSESN",
}

TickerFactory = Callable[[str], Any]


def unofficial_data_allowed(env: Mapping[str, str]) -> bool:
    allow = env.get("ALLOW_UNOFFICIAL_DATA", "").strip().lower() in _TRUE
    app_env = env.get("APP_ENV", "").strip().lower()
    return allow and app_env in _NON_PRODUCTION


def ticker_for(ref: InstrumentRef) -> str:
    explicit = ref.id_for(NAME)
    if explicit:
        return explicit
    index = _INDEX_TICKERS.get(ref.tradingsymbol.upper())
    if index:
        return index
    if ref.exchange is Exchange.NSE:
        return f"{ref.tradingsymbol}.NS"
    if ref.exchange is Exchange.BSE:
        return f"{ref.tradingsymbol}.BO"
    raise InstrumentNotResolved(NAME, f"Yahoo has no Indian derivatives ({ref.display})")


def _default_factory(symbol: str) -> Any:
    try:
        import yfinance
    except ImportError:
        raise ProviderUnavailable(NAME, "yfinance is not installed") from None
    return yfinance.Ticker(symbol)


class YFinanceProvider:
    name = NAME
    capabilities = frozenset(
        {Capability.QUOTES, Capability.OHLC, Capability.INTRADAY, Capability.CORP_ACTIONS}
    )
    quality = DataQuality.UNOFFICIAL_DELAYED

    def __init__(self, env: Mapping[str, str], ticker_factory: TickerFactory | None = None) -> None:
        if not unofficial_data_allowed(env):
            raise UnofficialDataDisabled(
                "yfinance needs ALLOW_UNOFFICIAL_DATA=true and a non-production APP_ENV"
            )
        self._ticker = ticker_factory or _default_factory

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]:
        out: list[Quote] = []
        for ref in refs:
            info = self._fast_info(ticker_for(ref))
            last = p.dec(_get(info, "last_price"))
            if last is None:
                continue  # Yahoo returned nothing for this symbol
            prev = p.positive_or_none(p.dec(_get(info, "previous_close")))
            change = last - prev if prev is not None else None
            out.append(
                Quote(
                    instrument=ref,
                    last_price=last,
                    source=NAME,
                    quality=self.quality,
                    open=p.positive_or_none(p.dec(_get(info, "open"))),
                    high=p.positive_or_none(p.dec(_get(info, "day_high"))),
                    low=p.positive_or_none(p.dec(_get(info, "day_low"))),
                    prev_close=prev,
                    change=change,
                    change_pct=p.pct_change(change, prev),
                    volume=p.integer(_get(info, "last_volume")),
                )
            )
        return out

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        yf_interval = _INTERVALS.get(interval)
        if yf_interval is None:
            raise NotSupported(NAME, f"Yahoo has no {interval.value} candles")
        symbol = ticker_for(ref)
        start, end = p.aware(start).astimezone(IST), p.aware(end).astimezone(IST)
        frame = self._call(
            lambda: self._ticker(symbol).history(
                start=start, end=end, interval=yf_interval, auto_adjust=False, actions=False
            )
        )
        out: dict[datetime, Candle] = {}
        for ts, row in frame.iterrows():
            when = p.aware(ts.to_pydatetime()).astimezone(IST)
            values = [p.dec(row.get(k)) for k in ("Open", "High", "Low", "Close")]
            volume = p.integer(row.get("Volume"))
            o, h, low, c = values
            if o is None or h is None or low is None or c is None or volume is None:
                continue
            if start <= when <= end:
                out[when] = Candle(when, o, h, low, c, volume)
        return [out[ts] for ts in sorted(out)]

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]:
        raise NotSupported(NAME, "Yahoo has no instrument master")

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]:
        ticker = self._call(lambda: self._ticker(ticker_for(ref)))
        dividends = self._call(lambda: ticker.dividends)
        splits = self._call(lambda: ticker.splits)
        out: list[CorporateAction] = []
        for kind, series in (
            (CorporateActionKind.DIVIDEND, dividends),
            (CorporateActionKind.SPLIT, splits),
        ):
            for ts, value in series.items():
                ex_date = ts.date()
                amount = p.dec(value)
                if amount is None or not start <= ex_date <= end:
                    continue
                out.append(
                    CorporateAction(
                        instrument=ref,
                        kind=kind,
                        ex_date=ex_date,
                        source=NAME,
                        quality=self.quality,
                        amount=amount if kind is CorporateActionKind.DIVIDEND else None,
                        # Yahoo reports bonuses as splits (verify case by case).
                        ratio=amount if kind is CorporateActionKind.SPLIT else None,
                    )
                )
        return sorted(out, key=lambda a: (a.ex_date, a.kind.value))

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain:
        raise NotSupported(NAME, "Yahoo has no Indian option chains")

    def _fast_info(self, symbol: str) -> Any:
        return self._call(lambda: self._ticker(symbol).fast_info)

    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except (InstrumentNotResolved, ProviderUnavailable):
            raise
        # yfinance raises arbitrary exception types (HTTP, parsing, pandas); report them all
        # as "unavailable" without their text, which can include request URLs.
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(NAME, f"yfinance call failed ({type(e).__name__})") from None


def _get(info: Any, key: str) -> Any:
    try:
        return info[key]
    except (KeyError, TypeError):
        return None
