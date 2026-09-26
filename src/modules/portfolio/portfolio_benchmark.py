"""Portfolio vs benchmark (M2): excess return, information ratio, relative drawdown and
normalised value curves.

The portfolio series is rebuilt from each holding's daily K-lines at the *current*
quantities (an approximation that ignores trades within the window). The benchmark
defaults to NIFTY 50; its K-lines come from the user's broker like everything else.
"""

from __future__ import annotations

import logging

from src.platform.marketdata.collectors.kline_collector import (
    KlineCollector,
    KlineData,
    get_index_klines,
)
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)

# Benchmark symbol (as the India bridge understands it) -> label.
BENCHMARKS: dict[str, str] = {
    "NIFTY 50": "NIFTY 50",
    "NIFTY BANK": "NIFTY BANK",
    "NIFTY NEXT 50": "NIFTY NEXT 50",
    "BSE:SENSEX": "SENSEX",
}
DEFAULT_BENCHMARK = "NIFTY 50"
_ANNUALIZE = 248  # approximate NSE trading days per year


def benchmark_label(code: str) -> str:
    return BENCHMARKS.get(code, code)


def compute_benchmark_metrics(
    dates: list[str],
    portfolio_values: list[float],
    benchmark_values: list[float],
    *,
    annualize: int = _ANNUALIZE,
) -> dict | None:
    """两条等长、按日期对齐的净值序列 → 对比指标 + 归一化曲线(归一到 100)。

    无效(长度 <2 / 不等长 / 起点非正)返回 None。
    """
    n = len(portfolio_values)
    if n < 2 or len(benchmark_values) != n or len(dates) != n:
        return None
    p0, b0 = portfolio_values[0], benchmark_values[0]
    if p0 <= 0 or b0 <= 0:
        return None

    pnorm = [v / p0 * 100 for v in portfolio_values]
    bnorm = [v / b0 * 100 for v in benchmark_values]
    port_return = portfolio_values[-1] / p0 - 1
    bench_return = benchmark_values[-1] / b0 - 1

    rp = [portfolio_values[i] / portfolio_values[i - 1] - 1 for i in range(1, n)]
    rb = [benchmark_values[i] / benchmark_values[i - 1] - 1 for i in range(1, n)]
    excess_daily = [a - b for a, b in zip(rp, rb)]
    mean_excess = sum(excess_daily) / len(excess_daily)
    var = sum((x - mean_excess) ** 2 for x in excess_daily) / len(excess_daily)
    std = var**0.5
    info_ratio = (mean_excess / std * (annualize**0.5)) if std > 0 else 0.0

    # 相对回撤:组合/基准 归一比值序列的最大回撤
    ratio = [pn / bn for pn, bn in zip(pnorm, bnorm)]
    peak, max_dd = ratio[0], 0.0
    for r in ratio:
        peak = max(peak, r)
        max_dd = min(max_dd, r / peak - 1)

    curve = [
        {"date": d, "portfolio": round(pn, 2), "benchmark": round(bn, 2)}
        for d, pn, bn in zip(dates, pnorm, bnorm)
    ]
    return {
        "portfolio_return": round(port_return * 100, 2),
        "benchmark_return": round(bench_return * 100, 2),
        "excess_return": round((port_return - bench_return) * 100, 2),
        "information_ratio": round(info_ratio, 2),
        "relative_drawdown": round(max_dd * 100, 2),
        "curve": curve,
        "days": n,
    }


def _fetch_benchmark_series(code: str, days: int) -> tuple[list[str], list[float]]:
    """Benchmark index daily closes -> (dates, closes); ([], []) on failure."""
    bars = get_index_klines(code, MarketCode.IN, days=int(days))
    return [b.date for b in bars], [b.close for b in bars]


def _ffill_closes(bars: list[KlineData], dates: list[str]) -> list[float]:
    """把持仓日K前向填充到给定(升序)交易日序列上。dates 均 >= bars 首日。"""
    series = sorted(((b.date, b.close) for b in bars), key=lambda x: x[0])
    out: list[float] = []
    last = series[0][1]
    j = 0
    for d in dates:
        while j < len(series) and series[j][0] <= d:
            last = series[j][1]
            j += 1
        out.append(last)
    return out


def build_portfolio_benchmark(
    holdings: list[dict],
    *,
    days: int = 60,
    benchmark_code: str = DEFAULT_BENCHMARK,
    kline_fetch=None,
) -> dict | None:
    """holdings: [{symbol, market, quantity, fx}] → 基准对比结果(含归一化曲线)。

    kline_fetch(symbol, market) -> list[KlineData];默认用 KlineCollector(带缓存)。
    """
    bench_dates, bench_closes = _fetch_benchmark_series(benchmark_code, days)
    if len(bench_dates) < 2:
        return None

    def _default_fetch(symbol: str, market: str) -> list[KlineData]:
        try:
            return KlineCollector(MarketCode(market)).get_klines(symbol, days=days + 10)
        except Exception:
            return []

    fetch = kline_fetch or _default_fetch

    holding_series = []
    for h in holdings:
        bars = fetch(h["symbol"], h["market"]) or []
        if bars:
            holding_series.append((h, bars, min(b.date for b in bars)))
    if not holding_series:
        return None

    # 所有持仓都有数据的起点,避免早期持仓缺数导致 NAV 失真;
    # 但覆盖极差的单只持仓(坏源/新股,只有最近 1-2 根)不许一票否决整个窗口:
    # 保底窗口 = max(10, 基准天数一半),覆盖不到保底窗口起点的持仓剔除出 NAV(记入 excluded)。
    min_window = max(10, len(bench_dates) // 2)
    floor_date = bench_dates[-min_window] if len(bench_dates) >= min_window else bench_dates[0]
    kept = [hs for hs in holding_series if hs[2] <= floor_date]
    excluded = [hs[0]["symbol"] for hs in holding_series if hs[2] > floor_date]
    if not kept:
        return None

    start_date = max(hs[2] for hs in kept)
    dates = [d for d in bench_dates if d >= start_date]
    if len(dates) < 2:
        return None

    nav = [0.0] * len(dates)
    for h, bars, _ in kept:
        closes = _ffill_closes(bars, dates)
        qfx = float(h.get("quantity", 0)) * float(h.get("fx", 1.0))
        for k in range(len(dates)):
            nav[k] += closes[k] * qfx

    bench_map = dict(zip(bench_dates, bench_closes))
    bench_vals = [bench_map[d] for d in dates]
    metrics = compute_benchmark_metrics(dates, nav, bench_vals)
    if metrics:
        metrics["benchmark_code"] = benchmark_code
        metrics["benchmark_label"] = benchmark_label(benchmark_code)
        if excluded:
            # 覆盖不足被剔除的持仓(如坏源/新股),供上层展示"基于 N-x 只计算"
            metrics["excluded"] = excluded
    return metrics


def build_attribution(
    holdings: list[dict],
    *,
    days: int = 60,
    benchmark_code: str = DEFAULT_BENCHMARK,
    kline_fetch=None,
) -> list[dict]:
    """近 days 日各持仓对组合收益的贡献(weight×return),按贡献降序。

    contribution_i ≈ 起始权重_i × 区间收益_i;和≈组合收益。用于"谁拖累/贡献"。
    """
    bench_dates, _ = _fetch_benchmark_series(benchmark_code, days)
    if len(bench_dates) < 2:
        return []

    def _default_fetch(symbol: str, market: str) -> list[KlineData]:
        try:
            return KlineCollector(MarketCode(market)).get_klines(symbol, days=days + 10)
        except Exception:
            return []

    fetch = kline_fetch or _default_fetch

    series = []
    for h in holdings:
        bars = fetch(h["symbol"], h["market"]) or []
        if bars:
            series.append((h, bars, min(b.date for b in bars)))
    if not series:
        return []

    start_date = max(s[2] for s in series)
    dates = [d for d in bench_dates if d >= start_date]
    if len(dates) < 2:
        return []

    tmp = []
    nav_start = 0.0
    for h, bars, _ in series:
        closes = _ffill_closes(bars, dates)
        qfx = float(h.get("quantity", 0)) * float(h.get("fx", 1.0))
        v_start = closes[0] * qfx
        nav_start += v_start
        r = (closes[-1] / closes[0] - 1) if closes[0] > 0 else 0.0
        tmp.append((h, v_start, r))
    if nav_start <= 0:
        return []

    rows = []
    for h, v_start, r in tmp:
        w = v_start / nav_start
        rows.append(
            {
                "symbol": h["symbol"],
                "name": h.get("name") or h["symbol"],
                "market": h["market"],
                "return_pct": round(r * 100, 2),
                "weight_pct": round(w * 100, 2),
                "contribution_pct": round(w * r * 100, 2),
            }
        )
    rows.sort(key=lambda x: x["contribution_pct"], reverse=True)
    return rows
