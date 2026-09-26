"""Stock metadata injection: the model must use the company name, not guess from the ticker."""

from __future__ import annotations

from src.modules.automation.tradingagents.data_context import build_stock_metadata_context
from src.modules.automation.tradingagents.toolkit_adapter import (
    _stock_meta_header,
    _serve_from_panwatch,
    panwatch_data_context,
)


class _FakeStock:
    def __init__(self, name, symbol, market_value):
        self.name = name
        self.symbol = symbol
        self.market = type("M", (), {"value": market_value})()


def test_metadata_context_has_company_name():
    """The meta context must include the company name, so the LLM doesn't make one up."""
    ctx = build_stock_metadata_context(
        stock_symbol="TATAMOTORS",
        stock_name="Tata Motors",
        market="IN",
        current_price=83.26,
    )
    assert "Tata Motors" in ctx
    assert "TATAMOTORS" in ctx
    assert "India (NSE/BSE)" in ctx
    assert "Indian (NSE/BSE) ticker" in ctx
    assert "83.26" in ctx
    assert "DO NOT guess" in ctx  # hard constraint


def test_metadata_context_includes_industry():
    """An industry, if present, is added to the context."""
    ctx = build_stock_metadata_context(
        stock_symbol="TATAMOTORS",
        stock_name="Tata Motors",
        market="IN",
        industry="Automobiles",
    )
    assert "Automobiles" in ctx


def test_metadata_context_empty_symbol_returns_empty():
    """Empty symbol -> empty string."""
    assert build_stock_metadata_context(stock_symbol="") == ""


def test_metadata_context_unknown_market_label_passes_through():
    ctx = build_stock_metadata_context(stock_symbol="X", stock_name="X Ltd", market="ZZ")
    assert "Market: ZZ" in ctx


def test_stock_meta_header_from_cache():
    """The tool result prefix includes the company name (from data injected by panwatch_data_context)."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    quote = {"current_price": 83.26, "change_pct": -2.5, "industry": "Automobiles"}
    with panwatch_data_context({"stock": stock, "quote": quote}):
        header = _stock_meta_header("TATAMOTORS")
    assert "Tata Motors" in header
    assert "TATAMOTORS" in header
    assert "India (NSE/BSE)" in header
    assert "83.26" in header
    assert "Automobiles" in header
    assert "DO NOT guess" in header


def test_serve_fundamentals_includes_company_name():
    """The fundamentals tool result must carry the company name, so the LLM can't mistake TATAMOTORS for another company."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_fundamentals_openai", "TATAMOTORS", {})
    assert "Tata Motors" in result
    assert "TATAMOTORS" in result


def test_serve_news_empty_does_not_leak_global_news():
    """With no news, the tool says plainly there is no stock news, stopping the LLM from pulling unrelated global news."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    with panwatch_data_context({"stock": stock, "events": []}):
        result = _serve_from_panwatch("get_news", "601238", {})
    assert "Tata Motors" in result
    assert "DO NOT pull unrelated global news" in result


def test_serve_klines_empty_returns_company_aware_message():
    """With no K-lines, a clear empty message with the company name is returned."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    with panwatch_data_context({"stock": stock, "klines": []}):
        result = _serve_from_panwatch("get_stockstats_indicators", "TATAMOTORS", {})
    assert "Tata Motors" in result
    assert "TATAMOTORS" in result


def test_serve_get_balance_sheet_hits_with_balance_keyword():
    """get_balance_sheet must match (there used to be no balance keyword, so it MISSED)."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_balance_sheet", "TATAMOTORS", {})
    assert "TATAMOTORS" in result
    assert "Balance sheet" in result
    assert "Avoid invented" in result


def test_serve_get_cashflow_distinct_from_balance_sheet():
    """get_cashflow returns its own content instead of reusing the balance sheet text."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        bs = _serve_from_panwatch("get_balance_sheet", "TATAMOTORS", {})
        cf = _serve_from_panwatch("get_cashflow", "TATAMOTORS", {})
    assert "Cash flow" in cf
    assert bs != cf  # must not be identical


def test_serve_get_stock_data_hits():
    """get_stock_data must match (the method keywords used to lack stock_data)."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_stock_data", "TATAMOTORS", {})
    assert "2026-05-15" in result  # CSV matched


def test_serve_get_indicators_without_args_fallback_to_kline_csv():
    """get_indicators without an indicator argument (rare) falls back to the K-line CSV."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    # Empty args -> the single-indicator branch doesn't match, so the stockstats/yfin branch returns the full CSV
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_indicators", "TATAMOTORS", {}, args=())
    # No single indicator matched, so it falls back to the stockstats branch -> the full K-line CSV
    assert "2026-05-15" in result


def test_serve_fundamentals_uses_real_quote_data():
    """get_fundamentals fills in real quote data (PE / market cap) instead of empty text."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    quote = {
        "current_price": 83.26,
        "pe_ratio": 25.5,
        "total_market_value": 125_000_000_000,
        "turnover_rate": 3.2,
    }
    with panwatch_data_context({"stock": stock, "quote": quote}):
        result = _serve_from_panwatch("get_fundamentals", "TATAMOTORS", {})
    assert "25.5" in result  # PE
    assert "125000000000" in result or "1.25e" in result.lower()  # market cap
    assert "3.2" in result  # turnover rate
    assert "Lightweight Fundamentals" in result


def test_serve_klines_with_data_returns_csv():
    """With K-line data, a CSV is returned, prefixed with the company name."""
    stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83.26, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines}):
        result = _serve_from_panwatch("get_stockstats_indicators", "TATAMOTORS", {})
    assert "Tata Motors" in result
    assert "2026-05-15,80,85,79,83.26,1000" in result


def test_patch_route_to_vendor_handles_positional_args():
    """Root-cause fix: upstream calls route_to_vendor(method, ticker, ...) positionally,
    so the patch must accept *args, otherwise a TypeError lets the call through to yfinance."""
    import sys
    from unittest.mock import MagicMock
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    # Build a fake tradingagents.dataflows.interface module for the test
    fake_ti = MagicMock()
    captured_calls = []

    def original_func(method, *args, **kwargs):
        captured_calls.append((method, args, kwargs))
        return "ORIGINAL_RESULT"

    fake_ti.route_to_vendor = original_func
    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func

    # Patch sys.modules so toolkit_adapter's import gets our fake module
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
        with panwatch_data_context({"stock": stock, "klines": [], "quote": {}}):
            with patch_route_to_vendor():
                # Simulate the upstream positional call: route_to_vendor("get_fundamentals", "TATAMOTORS", "2026-05-17")
                result = fake_module.route_to_vendor("get_fundamentals", "TATAMOTORS", "2026-05-17")

        # Our patch must recognise the positional ticker without a TypeError
        assert "Tata Motors" in result
        assert "TATAMOTORS" in result
        # It must not pass through to original (that would add to captured_calls)
        assert len(captured_calls) == 0
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)


def test_patch_route_to_vendor_intercepts_global_news_with_cache():
    """get_global_news(curr_date, look_back_days, limit) has no symbol,
    but when the cache holds a stock it must be intercepted, avoiding unrelated global news from Yahoo."""
    import sys
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    def original_func(method, *args, **kwargs):
        return "GLOBAL_SHOE_NEWS_LEAKED"

    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("Tata Motors", "TATAMOTORS", "IN")
        with panwatch_data_context({"stock": stock, "events": [], "quote": {}}):
            with patch_route_to_vendor():
                # get_global_news's first argument is a date, not a ticker
                result = fake_module.route_to_vendor(
                    "get_global_news", "2026-05-17", 7, 20
                )
        assert "GLOBAL_SHOE_NEWS_LEAKED" not in result
        assert "DO NOT pull unrelated global news" in result
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)
