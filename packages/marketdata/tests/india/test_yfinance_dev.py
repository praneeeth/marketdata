"""yfinance dev adapter: environment gate, unofficial tagging, parsing, error hygiene."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from marketdata.india import (
    IST,
    CorporateActionKind,
    DataQuality,
    Exchange,
    InstrumentNotResolved,
    InstrumentRef,
    Interval,
    NotSupported,
    ProviderUnavailable,
    UnofficialDataDisabled,
)
from marketdata.india import yfinance_dev as yf

from .fake_yfinance import FakeTicker
from .harness import session_for
from .providers import DEV_ENV, YFINANCE

INFY = InstrumentRef(Exchange.NSE, "INFY")


@pytest.mark.parametrize(
    ("env", "allowed"),
    [
        ({"ALLOW_UNOFFICIAL_DATA": "true", "APP_ENV": "development"}, True),
        ({"ALLOW_UNOFFICIAL_DATA": " YES ", "APP_ENV": "Test"}, True),
        ({"ALLOW_UNOFFICIAL_DATA": "true", "APP_ENV": "production"}, False),
        ({"ALLOW_UNOFFICIAL_DATA": "true", "APP_ENV": "staging"}, False),
        ({"ALLOW_UNOFFICIAL_DATA": "true"}, False),  # missing APP_ENV counts as production
        ({"ALLOW_UNOFFICIAL_DATA": "false", "APP_ENV": "development"}, False),
        ({"APP_ENV": "development"}, False),
        ({}, False),
    ],
)
def test_environment_gate(env: dict[str, str], allowed: bool) -> None:
    assert yf.unofficial_data_allowed(env) is allowed
    if allowed:
        yf.YFinanceProvider(env, ticker_factory=FakeTicker)
    else:
        with pytest.raises(UnofficialDataDisabled):
            yf.YFinanceProvider(env, ticker_factory=FakeTicker)


@pytest.mark.parametrize(
    ("ref", "ticker"),
    [
        (InstrumentRef(Exchange.NSE, "INFY"), "INFY.NS"),
        (InstrumentRef(Exchange.BSE, "INFY"), "INFY.BO"),
        (InstrumentRef(Exchange.NSE, "NIFTY 50"), "^NSEI"),
        (InstrumentRef(Exchange.BSE, "SENSEX"), "^BSESN"),
        (InstrumentRef(Exchange.NSE, "X").with_id("yfinance", "CUSTOM.NS"), "CUSTOM.NS"),
    ],
)
def test_ticker_mapping(ref: InstrumentRef, ticker: str) -> None:
    assert yf.ticker_for(ref) == ticker


def test_derivatives_are_not_supported() -> None:
    with pytest.raises(InstrumentNotResolved):
        yf.ticker_for(InstrumentRef(Exchange.NFO, "NIFTY26OCTFUT"))


def test_everything_is_tagged_unofficial() -> None:
    p, _ = YFINANCE.build()
    (q,) = p.quotes(session_for("yfinance"), [INFY])
    assert q.quality is DataQuality.UNOFFICIAL_DELAYED
    assert q.change_pct == Decimal("1.68")
    actions = p.corporate_actions(
        session_for("yfinance"), INFY, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert {a.quality for a in actions} == {DataQuality.UNOFFICIAL_DELAYED}


def test_unknown_symbol_returns_no_quote() -> None:
    p, _ = YFINANCE.build()
    assert p.quotes(session_for("yfinance"), [InstrumentRef(Exchange.NSE, "NOPE")]) == []


def test_candles_sorted_and_filtered() -> None:
    p, _ = YFINANCE.build()
    candles = p.candles(
        session_for("yfinance"),
        INFY,
        Interval.MINUTE_5,
        datetime(2026, 9, 23, 9, 16, tzinfo=IST),
        datetime(2026, 9, 23, 15, 30, tzinfo=IST),
    )
    assert [c.ts.minute for c in candles] == [20, 25]


def test_unsupported_intervals() -> None:
    p, _ = YFINANCE.build()
    with pytest.raises(NotSupported, match="3m"):
        p.candles(session_for("yfinance"), INFY, Interval.MINUTE_3, YFINANCE.start, YFINANCE.end)


def test_corporate_actions() -> None:
    p, _ = YFINANCE.build()
    actions = p.corporate_actions(
        session_for("yfinance"), INFY, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert [(a.kind, a.ex_date) for a in actions] == [
        (CorporateActionKind.DIVIDEND, date(2026, 5, 30)),
        (CorporateActionKind.SPLIT, date(2026, 6, 15)),
    ]
    assert actions[0].amount == Decimal("21.0")
    assert actions[1].ratio == Decimal("2.0")
    assert actions[1].amount is None


class Exploding:
    def __init__(self, symbol: str) -> None:
        raise RuntimeError(f"GET https://query1.example/v8/{symbol}?crumb=SECRET_CRUMB")


def test_library_errors_are_wrapped_without_text() -> None:
    p = yf.YFinanceProvider(DEV_ENV, ticker_factory=Exploding)
    with pytest.raises(ProviderUnavailable) as exc:
        p.quotes(session_for("yfinance"), [INFY])
    assert "SECRET_CRUMB" not in str(exc.value)
    assert "RuntimeError" in str(exc.value)
    assert exc.value.__suppress_context__


def test_missing_library(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yfinance":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ProviderUnavailable, match="not installed"):
        yf.YFinanceProvider(DEV_ENV).quotes(session_for("yfinance"), [INFY])


def test_fast_info_key_errors() -> None:
    assert yf._get({}, "x") is None
    assert yf._get(None, "x") is None


class LazyFailingInfo:
    """yfinance's FastInfo fetches on first key access, not on attribute access."""

    def __getitem__(self, key: str) -> Any:
        raise ConnectionError("GET https://query2.example/v7?crumb=SECRET_CRUMB failed")


class LazyTicker:
    def __init__(self, symbol: str) -> None:
        self.fast_info = LazyFailingInfo()


def test_lazy_fast_info_errors_are_wrapped() -> None:
    """Review #6: network errors raised on key access must become ProviderUnavailable."""
    p = yf.YFinanceProvider(DEV_ENV, ticker_factory=LazyTicker)
    with pytest.raises(ProviderUnavailable) as exc:
        p.quotes(session_for("yfinance"), [INFY])
    assert "SECRET_CRUMB" not in str(exc.value)
