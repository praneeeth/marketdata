"""Shared HTTP helper: retries and the fetch-source label in failure logs."""

from __future__ import annotations

import logging

from src.platform.marketdata.collectors import market_http
from src.platform.marketdata.models import MarketCode


def test_market_get_retries_and_logs_source(monkeypatch, caplog):
    """market_get 失败应退避重试,并在日志带上 [src=...] 调用来源。"""
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(market_http.httpx, "Client", _FakeClient)
    monkeypatch.setattr(market_http.time, "sleep", lambda *_: None)

    with caplog.at_level(logging.WARNING):
        with market_http.fetch_source("unit_src"):
            out = market_http.market_get(
                "http://x", host_key="x", retries=2, log_label="测试"
            )

    assert out is None
    assert calls["n"] == 3, f"应 1 次 + 重试 2 次 = 3 次,实际 {calls['n']}"
    assert any(
        "[src=unit_src]" in r.getMessage() for r in caplog.records
    ), caplog.text
