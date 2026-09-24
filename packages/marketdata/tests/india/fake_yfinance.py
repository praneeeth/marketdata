"""An offline stand-in for ``yfinance.Ticker`` built on real pandas objects."""

from __future__ import annotations

from typing import Any

import pandas as pd

TICKERS: dict[str, dict[str, Any]] = {
    "INFY.NS": {
        "fast_info": {
            "last_price": 1412.95,
            "previous_close": 1389.65,
            "open": 1396.0,
            "day_high": 1421.75,
            "day_low": 1395.55,
            "last_volume": 7360198,
        },
        "history": pd.DataFrame(
            {
                "Open": [1401.0, 1396.0, 1404.1],
                "High": [1405.0, 1402.5, 1406.0],
                "Low": [1399.0, 1395.55, 1400.2],
                "Close": [1404.1, 1401.0, 1403.0],
                "Volume": [180000, 250000, 150000],
            },
            index=pd.DatetimeIndex(
                ["2026-09-23 09:20", "2026-09-23 09:15", "2026-09-23 09:25"]
            ).tz_localize("Asia/Kolkata"),
        ),
        "dividends": pd.Series(
            [20.0, 21.0],
            index=pd.DatetimeIndex(["2025-10-24", "2026-05-30"]).tz_localize("Asia/Kolkata"),
        ),
        "splits": pd.Series(
            [2.0], index=pd.DatetimeIndex(["2026-06-15"]).tz_localize("Asia/Kolkata")
        ),
    },
}


class FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._data = TICKERS.get(symbol, {})

    @property
    def fast_info(self) -> dict[str, Any]:
        return self._data.get("fast_info", {})

    def history(self, **_kwargs: Any) -> pd.DataFrame:
        return self._data.get("history", pd.DataFrame())

    @property
    def dividends(self) -> pd.Series:
        return self._data.get("dividends", pd.Series(dtype=float))

    @property
    def splits(self) -> pd.Series:
        return self._data.get("splits", pd.Series(dtype=float))
