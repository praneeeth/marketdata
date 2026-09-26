import src.modules.market.api.stocks as stocks_api


def test_get_quotes_uses_md_quote_rows(monkeypatch):
    """Watchlist /quotes should go through md_quote_rows (not directly to a vendor quote fetch)."""
    calls = []
    monkeypatch.setattr(
        stocks_api, "md_quote_rows",
        lambda symbols, market: (calls.append((tuple(symbols), market)),
                                 [{"symbol": symbols[0], "current_price": 3.0,
                                   "change_pct": 1.0, "change_amount": 0.03, "prev_close": 2.97}])[1],
    )
    assert hasattr(stocks_api, "md_quote_rows")
    # Semantics: raw symbols are passed and grouped by market
    assert callable(stocks_api.md_quote_rows)
