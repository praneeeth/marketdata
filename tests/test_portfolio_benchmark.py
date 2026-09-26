"""Portfolio vs benchmark (M2): excess return / information ratio / relative drawdown + NAV curves."""

from __future__ import annotations

from src.platform.marketdata.collectors.kline_collector import KlineData
from src.modules.portfolio import portfolio_benchmark as pb


def _bars(dates_closes):
    return [
        KlineData(date=d, open=c, close=c, high=c, low=c, volume=0) for d, c in dates_closes
    ]


def test_metrics_outperform_flat_benchmark():
    """Benchmark flat, portfolio rising -> positive excess, positive information ratio, relative drawdown ≈ 0."""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
    port = [100, 101, 102, 103, 104]
    bench = [100, 100, 100, 100, 100]
    m = pb.compute_benchmark_metrics(dates, port, bench)
    assert m is not None
    assert m["portfolio_return"] == 4.0
    assert m["benchmark_return"] == 0.0
    assert m["excess_return"] == 4.0
    assert m["information_ratio"] > 0
    assert m["relative_drawdown"] == 0.0
    assert len(m["curve"]) == 5 and m["curve"][0]["portfolio"] == 100.0


def test_metrics_identical_series_zero_excess():
    """Portfolio identical to the benchmark -> excess 0, information ratio 0, relative drawdown 0."""
    dates = ["d1", "d2", "d3"]
    s = [100, 105, 103]
    m = pb.compute_benchmark_metrics(dates, list(s), list(s))
    assert m["excess_return"] == 0.0
    assert m["information_ratio"] == 0.0
    assert m["relative_drawdown"] == 0.0


def test_metrics_invalid_returns_none():
    """Too short / unequal lengths -> None (no exception)."""
    assert pb.compute_benchmark_metrics(["d1"], [100], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [100, 101], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [0, 101], [100, 101]) is None


def test_build_portfolio_benchmark_with_mocked_fetch(monkeypatch):
    """Portfolio flat, benchmark rising -> negative excess; benchmark metadata filled in."""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04"]
    monkeypatch.setattr(
        pb, "_fetch_benchmark_series", lambda code, days: (dates, [100.0, 110.0, 121.0])
    )

    def fake_fetch(symbol, market):
        return _bars([(d, 10.0) for d in dates])  # holdings flat

    res = pb.build_portfolio_benchmark(
        [{"symbol": "600519", "market": "IN", "quantity": 100, "fx": 1.0}],
        days=60,
        benchmark_code="NIFTY 50",
        kline_fetch=fake_fetch,
    )
    assert res is not None
    assert res["benchmark_code"] == "NIFTY 50"
    assert res["benchmark_label"] == "NIFTY 50"
    assert res["portfolio_return"] == 0.0
    assert res["benchmark_return"] == 21.0
    assert res["excess_return"] == -21.0
    assert res["relative_drawdown"] < 0


def test_build_benchmark_excludes_poor_coverage_holding(monkeypatch):
    """One holding with very poor coverage (a bad source returning only the last bar) is excluded and listed in excluded, no longer vetoing the comparison."""
    dates = [f"2026-01-{d:02d}" for d in range(2, 14)]  # 12 trading days
    monkeypatch.setattr(
        pb, "_fetch_benchmark_series",
        lambda code, days: (dates, [100.0 + i for i in range(len(dates))]),
    )

    def fake_fetch(symbol, market):
        if symbol == "BABA":
            return _bars([(dates[-1], 200.0)])  # only the last bar -> poor coverage
        return _bars([(d, 10.0 + i * 0.1) for i, d in enumerate(dates)])

    res = pb.build_portfolio_benchmark(
        [
            {"symbol": "600519", "market": "IN", "quantity": 100, "fx": 1.0},
            {"symbol": "BABA", "market": "US", "quantity": 10, "fx": 7.0},
        ],
        days=60,
        kline_fetch=fake_fetch,
    )
    assert res is not None and len(res.get("curve") or []) >= 2
    assert res.get("excluded") == ["BABA"]
