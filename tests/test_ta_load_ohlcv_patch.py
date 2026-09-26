"""TA load_ohlcv takeover: Indian stocks use PanWatch K-lines; US stocks pass through to yfinance.

The newer upstream get_verified_market_snapshot -> load_ohlcv goes straight to yfinance, which can't fetch symbols without
an exchange suffix -> NoMarketDataError fails the whole analysis. This checks PanWatch's takeover builds OHLCV for them without affecting US stocks.
"""

from __future__ import annotations

from datetime import date, timedelta
import threading

import pandas as pd
import pytest

from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.platform.marketdata.collectors.kline_collector import KlineCollector, KlineData


def _sample_klines(n: int = 40) -> list[KlineData]:
    base = date(2026, 4, 1)
    return [
        KlineData(
            date=str(base + timedelta(days=i)),
            open=1.0 + i,
            close=2.0 + i,
            high=3.0 + i,
            low=0.5 + i,
            volume=100.0 + i,
        )
        for i in range(n)
    ]


def test_build_df_columns_and_date_filter(monkeypatch):
    """The built DataFrame has Date/OHLCV columns, Date as datetime, truncated at curr_date."""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(40))
    df = ta._build_panwatch_ohlcv_df("601238", "2026-04-20")
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert str(df["Date"].dtype).startswith("datetime64")
    assert (df["Date"] <= pd.to_datetime("2026-04-20")).all()
    assert len(df) == 20  # 04-01..04-20


def test_build_df_reuses_injected_klines_before_fetching_again(monkeypatch):
    """The verification snapshot reuses the K-lines from the collection stage, so analysts don't make another round of external requests."""
    cached = _sample_klines(12)

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("should reuse PanWatch K-lines already in context")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": cached}):
        df = ta._build_panwatch_ohlcv_df("601238", "2026-04-20")

    assert len(df) == 12


def test_build_df_reuses_empty_injected_klines_without_retrying(monkeypatch):
    """When the collection stage already found no K-lines, later tools don't retry the same symbol over the network."""
    calls = []

    def unexpected_fetch(self, symbol, days=60):
        calls.append((symbol, days))
        raise AssertionError("known empty snapshot must not trigger another fetch")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": []}):
        assert ta._build_panwatch_ohlcv_df("601238", "2026-04-20") is None

    assert calls == []


def test_build_df_does_not_reuse_klines_for_another_symbol(monkeypatch):
    """When the model passes another symbol by mistake, the current symbol's cache must not pose as that symbol's data."""
    cached = _sample_klines(12)
    fetched = _sample_klines(8)
    calls = []

    def fetch(self, symbol, days=60):
        calls.append((symbol, days))
        return fetched

    monkeypatch.setattr(KlineCollector, "get_klines", fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": cached}):
        df = ta._build_panwatch_ohlcv_df("300624", "2026-04-20")

    assert len(df) == 8
    assert calls == [("300624", 750)]


def test_cancelled_ta_context_does_not_fetch_another_symbol(monkeypatch):
    """After the task times out, a leftover worker calling the quote tool must stop at once."""
    calls = []

    def unexpected_fetch(self, symbol, days=60):
        calls.append((symbol, days))
        raise AssertionError("cancelled task must not fetch another symbol")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "300624"})()
    cancel_event = threading.Event()
    cancel_event.set()

    from src.modules.automation.tradingagents.toolkit_adapter import (
        TradingAgentsCancelled,
        panwatch_data_context,
    )

    with panwatch_data_context(
        {"stock": stock, "klines": _sample_klines(12)},
        cancel_event=cancel_event,
    ):
        with pytest.raises(TradingAgentsCancelled):
            ta._build_panwatch_ohlcv_df("300624", "2026-04-20")

    assert calls == []


def test_load_ohlcv_routes_a_share_to_panwatch(monkeypatch):
    """Every ticker's candles come from PanWatch (the broker), never native yfinance."""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(10))
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    df = ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert not df.empty
    assert real_calls["n"] == 0, "must not fall back to yfinance"


def test_verified_snapshot_returns_unavailable_message_when_all_sources_fail(monkeypatch):
    """When every quote source fails, the verification snapshot returns an unavailable message instead of raising into LangGraph."""
    from tradingagents.dataflows.errors import NoMarketDataError

    def no_data(*args, **kwargs):
        raise NoMarketDataError("AAPL", "AAPL", "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot("AAPL", "2026-06-18")

    assert "Verified market data unavailable for AAPL" in out
    assert "Do not make exact price, indicator, stop-loss, or trade-action claims" in out


def test_verified_snapshot_preserves_indicators_argument_when_degraded(monkeypatch):
    """The safe wrapper must keep upstream's indicators parameter so callers don't break on a signature change."""
    from tradingagents.dataflows.errors import NoMarketDataError

    seen = {}

    def no_data(symbol, curr_date, look_back_days=30, indicators=None):
        seen["indicators"] = indicators
        raise NoMarketDataError(symbol, symbol, "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot(
        "AAPL", "2026-06-18", indicators=("rsi",)
    )

    assert "Verified market data unavailable for AAPL" in out
    assert seen == {"indicators": ("rsi",)}


def test_install_load_ohlcv_patch_updates_yfinance_indicator_import(monkeypatch):
    """The load_ohlcv reference held by the technical indicator tool must also use the same US fallback."""
    from tradingagents.dataflows import market_data_validator, stockstats_utils, y_finance

    def upstream_load_ohlcv(*args, **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_LOAD_OHLCV_PATCHED", False)
    monkeypatch.setattr(ta, "_real_load_ohlcv", None)
    for module in (stockstats_utils, y_finance, market_data_validator):
        monkeypatch.setattr(module, "load_ohlcv", upstream_load_ohlcv)

    ta._ensure_load_ohlcv_patched()

    assert y_finance.load_ohlcv is ta._panwatch_load_ohlcv


def test_load_ohlcv_a_share_no_klines_raises_not_fallback(monkeypatch):
    """When K-lines can't be fetched, NoMarketDataError is raised with a clear message and there's **no fallback to yfinance**.

    Yahoo has no data (and rate-limits) for these symbols, so a fallback would only turn "K-line fetch failed" into a misleading "Yahoo no rows".
    """
    import pytest
    from tradingagents.dataflows.errors import NoMarketDataError

    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: [])
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    with pytest.raises(NoMarketDataError):
        ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert real_calls["n"] == 0, "an empty fetch must not fall back to yfinance"


def test_route_to_vendor_keeps_numeric_requested_symbol(monkeypatch):
    """An all-digit stock code is also a valid ticker; being all digits mustn't make it reuse the cached symbol."""
    stock = type("Stock", (), {"symbol": "300624"})()
    monkeypatch.setattr(
        ta,
        "_serve_from_panwatch",
        lambda method_name, symbol, kwargs, args=(): f"served:{symbol}",
    )

    with ta.panwatch_data_context({"stock": stock, "klines": _sample_klines(4)}):
        out = ta._patched_route_to_vendor("get_stock_data", "300624", "2026-06-18")

    assert out == "served:300624"


def test_route_to_vendor_rejects_cached_snapshot_for_different_numeric_symbol(monkeypatch):
    """When the cached snapshot and the requested symbol differ, one stock's data must never silently stand in for another."""
    stock = type("Stock", (), {"symbol": "601238"})()
    monkeypatch.setattr(
        ta,
        "_serve_from_panwatch",
        lambda method_name, symbol, kwargs, args=(): "wrong cached data",
    )

    with ta.panwatch_data_context({"stock": stock, "klines": _sample_klines(4)}):
        out = ta._patched_route_to_vendor("get_stock_data", "300624", "2026-06-18")

    assert "DATA_UNAVAILABLE" in out
    assert "300624" in out


def test_route_to_vendor_marks_expected_upstream_outage_as_data_unavailable(monkeypatch):
    """Known unavailable external data should give the LLM a clear signal instead of being swallowed as an empty string."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_DATA", "true")

    def boom(method_name, *a, **k):
        raise RuntimeError("FRED_API_KEY environment variable is not set")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)
    # Development only: the macro tool reaches the upstream vendor, whose outage becomes DATA_UNAVAILABLE.
    out = ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
    assert "DATA_UNAVAILABLE" in out
    assert "FRED_API_KEY" in out


def test_route_to_vendor_propagates_programming_errors(monkeypatch):
    """Contract/implementation errors mustn't pose as missing data, or they would hide upgrade regressions."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_DATA", "true")

    def boom(method_name, *a, **k):
        raise TypeError("unexpected keyword argument 'vendor'")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)

    import pytest
    with pytest.raises(TypeError, match="unexpected keyword"):
        ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)


def test_route_to_vendor_does_not_misclassify_generic_not_set_error(monkeypatch):
    """Only missing data source config may degrade; an unset internal state must still surface."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_DATA", "true")

    def boom(method_name, *a, **k):
        raise RuntimeError("internal state not set")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)

    import pytest
    with pytest.raises(RuntimeError, match="internal state not set"):
        ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)


def test_route_to_vendor_never_calls_upstream_in_production(monkeypatch):
    """Plan X8: outside development nothing falls through to Yahoo or other vendors."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_DATA", "true")
    calls = []
    monkeypatch.setattr(ta, "_real_route_to_vendor", lambda *a, **k: calls.append(a) or "data")
    out = ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
    assert "DATA_UNAVAILABLE" in out
    assert calls == []


def test_route_to_vendor_maps_only_the_tasks_stock_in_development(monkeypatch):
    """Dev fallthrough maps the task's ticker to Yahoo's .NS form, but not other arguments."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_UNOFFICIAL_DATA", "true")
    seen = []
    monkeypatch.setattr(ta, "_real_route_to_vendor", lambda method, *a, **k: seen.append((method, a)) or "ok")
    monkeypatch.setattr(ta, "_cached_symbol", lambda: "INFY")
    monkeypatch.setattr(ta, "_cache", lambda: {})
    ta._patched_route_to_vendor("get_insider_transactions", "INFY", "2026-06-18")
    ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
    assert seen[0] == ("get_insider_transactions", ("INFY.NS", "2026-06-18"))
    assert seen[1] == ("get_macro_indicators", ("fed_funds_rate", "2026-06-18", 30))
