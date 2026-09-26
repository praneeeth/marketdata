"""External quote links (src/modules/administration/stock_link.py), India-only.

Every test passes ``platform`` explicitly or relies on the conftest mock, so none touch
the database.
"""

from __future__ import annotations

import pytest

from src.modules.administration import stock_link
from src.modules.administration.stock_link import stock_link_markdown, stock_url


@pytest.mark.parametrize(
    ("symbol", "platform", "url"),
    [
        ("INFY", "nse", "https://www.nseindia.com/get-quotes/equity?symbol=INFY"),
        ("M&M", "nse", "https://www.nseindia.com/get-quotes/equity?symbol=M%26M"),
        ("NSE:INFY", "tradingview", "https://www.tradingview.com/symbols/NSE-INFY/"),
        ("BSE:500209", "tradingview", "https://www.tradingview.com/symbols/BSE-500209/"),
        ("INFY", "google", "https://www.google.com/finance/quote/INFY:NSE"),
        # NSE's site has no BSE pages, so BSE symbols use Google Finance (BOM = BSE).
        ("BSE:500209", "nse", "https://www.google.com/finance/quote/500209:BOM"),
        (" infy ", "nse", "https://www.nseindia.com/get-quotes/equity?symbol=INFY"),
    ],
)
def test_stock_url(symbol: str, platform: str, url: str) -> None:
    assert stock_url(symbol, "IN", platform=platform) == url


def test_unknown_or_legacy_platform_uses_the_setting() -> None:
    # conftest mocks get_platform() -> "nse"; "xueqiu" was the removed Chinese default.
    assert stock_url("INFY", platform="xueqiu").startswith("https://www.nseindia.com/")
    assert stock_url("INFY").startswith("https://www.nseindia.com/")


def test_markdown() -> None:
    assert (
        stock_link_markdown("INFY", platform="nse")
        == "[INFY](https://www.nseindia.com/get-quotes/equity?symbol=INFY)"
    )


def test_get_platform_falls_back_for_legacy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    class Row:
        value = "xueqiu"

    class Query:
        def filter(self, *_a: object) -> "Query":
            return self

        def first(self) -> Row:
            return Row()

    class Db:
        def query(self, *_a: object) -> Query:
            return Query()

        def close(self) -> None:
            pass

    monkeypatch.undo()  # drop conftest's get_platform mock for this test
    monkeypatch.setattr(stock_link, "SessionLocal", Db)
    assert stock_link.get_platform() == "nse"
    Row.value = "tradingview"
    assert stock_link.get_platform() == "tradingview"
