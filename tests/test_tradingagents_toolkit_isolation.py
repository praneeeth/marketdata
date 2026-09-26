"""Regression tests for TradingAgents toolkit data isolation under concurrency.

Root-cause bug: _PANWATCH_DATA_CACHE used to be a module-level global dict, so two concurrent deep research runs
(asyncio.to_thread worker threads) overwrote each other: one stock's report picked up the other's
K-lines/prices. With a ContextVar, each concurrent task (copy_context) gets its own copy and they don't mix.

This test simulates two concurrent tasks with contextvars.copy_context() to reproduce the bug and check the fix.
Note: it only tests the direct _serve_from_panwatch / _stock_meta_header paths, not
_patched_route_to_vendor (to avoid _emit_toolkit_log -> log_context/DB).
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace

from src.modules.automation.tradingagents import toolkit_adapter as ta


def _stock(symbol: str, name: str):
    return SimpleNamespace(symbol=symbol, name=name, market=SimpleNamespace(value="CN"))


def _kline(date: str, close: float):
    return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


def _data(symbol: str, name: str, close: float):
    return {
        "stock": _stock(symbol, name),
        "quote": {"name": name, "current_price": close},
        "klines": [_kline("2026-05-01", close), _kline("2026-05-02", close + 1)],
    }


GAC = _data("TATAMOTORS", "Tata Motors", 9.50)
SERES = _data("MARUTI", "Maruti Suzuki", 83.26)


def test_stock_meta_header_uses_current_context():
    """_stock_meta_header reads the stock from the current context, not a process global."""
    def _run():
        with ta.panwatch_data_context(GAC):
            return ta._stock_meta_header("TATAMOTORS")
    header = contextvars.copy_context().run(_run)
    assert "Tata Motors" in header
    assert "Maruti Suzuki" not in header
    assert "9.50" in header


def test_two_concurrent_contexts_do_not_cross_talk():
    """Reproduces the production bug: while task A (Tata Motors) runs, task B (Maruti Suzuki) injects its data;
    A's later tool calls must still read Tata Motors; the old global dict would have mixed in Maruti Suzuki here."""
    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()

    # A enters its context first (worker A starts; data injected but tools not run yet)
    ctx_a.run(lambda: ta._PANWATCH_DATA.set(dict(GAC)))
    # B enters its context next (concurrent task B starts); the old implementation overwrote the global here
    ctx_b.run(lambda: ta._PANWATCH_DATA.set(dict(SERES)))

    # A keeps calling tools: get_stock_data(TATAMOTORS) must return Tata Motors' K-lines/price
    out_a = ctx_a.run(lambda: ta._serve_from_panwatch("get_stock_data", "TATAMOTORS", {}, args=("TATAMOTORS",)))
    out_b = ctx_b.run(lambda: ta._serve_from_panwatch("get_stock_data", "MARUTI", {}, args=("MARUTI",)))

    assert "Tata Motors" in out_a and "Maruti Suzuki" not in out_a
    assert "9.5" in out_a            # Tata Motors' close
    assert "83.26" not in out_a      # not Maruti Suzuki's price

    assert "Maruti Suzuki" in out_b and "Tata Motors" not in out_b


def test_context_restored_after_exit():
    """After panwatch_data_context exits, the current context's data is empty again."""
    def _run():
        assert ta._cache() == {}
        with ta.panwatch_data_context(SERES):
            assert ta._cache().get("stock").symbol == "MARUTI"
        # Restored after exit
        return ta._cache()
    assert contextvars.copy_context().run(_run) == {}


def test_nested_contexts_restore_outer():
    """Nested contexts: after the inner one exits, the outer data comes back (token reset semantics)."""
    def _run():
        with ta.panwatch_data_context(GAC):
            assert ta._cache().get("stock").symbol == "TATAMOTORS"
            with ta.panwatch_data_context(SERES):
                assert ta._cache().get("stock").symbol == "MARUTI"
            # The inner one exited; the outer Tata Motors data is back
            assert ta._cache().get("stock").symbol == "TATAMOTORS"
    contextvars.copy_context().run(_run)


# ---------------------------------------------------------------------------
# Industry/theme news keyword search (feature B: get_news with a non-ticker word searches live news)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import unittest
    unittest.main()
