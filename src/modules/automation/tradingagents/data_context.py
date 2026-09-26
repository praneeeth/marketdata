"""TradingAgents data context adapters (India-only).

Converts PanWatch data into structures TradingAgents consumes: PortfolioContext, the
instrument context and stock metadata. The financial-statement renderers take a
normalised dict; the upstream akshare (A-share) fetcher was removed, and Indian
fundamentals depend on open question Q8, so the toolkit falls back to quote data.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# 统一导出“业务数据 → TradingAgents 上下文”的入口，避免调用方关心内部渲染函数。
__all__ = [
    "build_stock_metadata_context",
    "fetch_financial_abstract",
    "patch_instrument_context",
    "render_balance_sheet",
    "render_cashflow",
    "render_fundamentals_summary",
    "render_income_statement",
    "to_tradingagents_portfolio",
]


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "N/A"
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if av >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.2f}%"


def _fmt_period(p: str) -> str:
    """20260331 → 2026Q1, 20251231 → 2025Q4(年报)"""
    if len(p) != 8:
        return p
    y, m, d = p[:4], p[4:6], p[6:8]
    q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, m)
    return f"{y}{q}"


def render_fundamentals_summary(data: dict) -> str:
    """渲染基本面综合摘要(给 get_fundamentals 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No financial data available]"

    lines = ["[Reported financials]"]
    lines.append(f"Reporting periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")

    key_metrics = [
        ("营业总收入", _fmt_num),
        ("归母净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("基本每股收益", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("毛利率", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
        ("资产负债率", _fmt_pct),
        ("经营现金流量净额", _fmt_num),
    ]
    for name, fmt in key_metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")

    lines.append("")
    lines.append(
        "Note: This is REAL data from the company's reported financial statements. "
        "Use these numbers to ground your fundamental analysis (revenue trends, "
        "profitability, leverage). Do NOT invent additional numbers."
    )
    return "\n".join(lines)


def render_income_statement(data: dict) -> str:
    """渲染利润表(给 get_income_statement 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No income statement data]"
    lines = ["[Income statement]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("营业总收入", _fmt_num),
        ("营业成本", _fmt_num),
        ("归母净利润", _fmt_num),
        ("净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("毛利率", _fmt_pct),
        ("销售净利率", _fmt_pct),
        ("期间费用率", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_balance_sheet(data: dict) -> str:
    """渲染资产负债表(给 get_balance_sheet 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No balance sheet data]"
    lines = ["[Balance sheet]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("股东权益合计(净资产)", _fmt_num),
        ("每股净资产", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("商誉", _fmt_num),
        ("资产负债率", _fmt_pct),
        ("总资产报酬率(ROA)", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_cashflow(data: dict) -> str:
    """渲染现金流量表(给 get_cashflow 用)。"""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No cash flow data]"
    lines = ["[Cash flow statement]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("经营现金流量净额", _fmt_num),
        ("每股现金流", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


# ============================================================================
# Portfolio and instrument context
# ============================================================================

def build_stock_metadata_context(
    stock_symbol: str,
    stock_name: str = "",
    market: str = "IN",
    current_price: float | None = None,
    industry: str = "",
) -> str:
    """Stock metadata so the model never guesses the company from the ticker."""
    if not stock_symbol:
        return ""

    market_label = {"IN": "India (NSE/BSE)"}.get(market, market)
    lines = [
        "[Stock Metadata]",
        f"- Ticker: {stock_symbol}",
        f"- Company name: {stock_name or 'N/A'}",
        f"- Market: {market_label}",
    ]
    if industry:
        lines.append(f"- Industry: {industry}")
    if current_price and current_price > 0:
        lines.append(f"- Current price: {current_price:.2f}")
    lines.append(
        "- IMPORTANT: This is an Indian (NSE/BSE) ticker. DO NOT guess the "
        "company from the ticker code; always use the company name above."
    )
    return "\n".join(lines)


def _finite_number(value: Any) -> float | None:
    """把可能来自数据库的数值安全转换为有限 float。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_tradingagents_portfolio(portfolio: Any):
    """把 ``PortfolioInfo`` 转为 TradingAgents 0.5.0 的 ``PortfolioContext``。

    多账户中同一 ticker 的仓位按数量加权平均成本价聚合。没有账户快照时返回
    ``None``，让上游明确区分“用户未提供组合”与“组合现金/仓位均为零”。
    """
    accounts: Iterable[Any] = getattr(portfolio, "accounts", ()) or ()
    accounts = list(accounts)
    if not accounts:
        return None

    from tradingagents.portfolio import PortfolioContext, Position

    cash = 0.0
    by_ticker: dict[str, list[tuple[float, float | None]]] = {}
    for account in accounts:
        available_funds = _finite_number(getattr(account, "available_funds", 0))
        if available_funds is not None:
            cash += available_funds

        for position in getattr(account, "positions", ()) or ():
            ticker = str(getattr(position, "symbol", "") or "").strip().upper()
            quantity = _finite_number(getattr(position, "quantity", None))
            # TradingAgents 0.5.0 用正数表示多头、负数表示空头；这里只过滤
            # 零数量和脏数据，不能把空头当成“无持仓”丢掉。
            if not ticker or quantity is None or quantity == 0:
                continue
            average_price = _finite_number(getattr(position, "cost_price", None))
            by_ticker.setdefault(ticker, []).append((quantity, average_price))

    positions = []
    for ticker, lots in by_ticker.items():
        quantity = sum(lot_quantity for lot_quantity, _ in lots)
        priced_lots = [
            (lot_quantity, average_price)
            for lot_quantity, average_price in lots
            if average_price is not None
        ]
        # 用数量绝对值做成本价权重：同方向仓位与旧逻辑一致，混合多空时
        # 也不会因净数量接近 0 而产生无意义的极端均价；quantity 仍保留净符号。
        total_abs_quantity = sum(abs(lot_quantity) for lot_quantity, _ in priced_lots)
        average_price = (
            sum(abs(lot_quantity) * price for lot_quantity, price in priced_lots)
            / total_abs_quantity
            if len(priced_lots) == len(lots) and total_abs_quantity > 0
            else None
        )
        positions.append(
            Position(ticker=ticker, quantity=quantity, average_price=average_price)
        )

    return PortfolioContext(cash=cash, positions=positions)


def patch_instrument_context(graph: Any, metadata_context: str) -> None:
    """把 PanWatch 标的元数据注入 TradingAgents 0.5.0 的 ``instrument_context``。

    ``past_context`` 是上游用于历史研究记忆的扩展点，业务标的元数据放进去会
    混淆提示词语义，也会让后续研究回放把本次股票信息当成历史经验。0.5.0 的
    ``Propagator.create_initial_state`` 已公开 ``instrument_context``，因此只在
    这个入口做一次实例级包装，并完整透传 portfolio/future kwargs。
    """
    if not metadata_context:
        return

    propagator = getattr(graph, "propagator", None)
    if propagator is None or not hasattr(propagator, "create_initial_state"):
        logger.warning("[TA context] propagator.create_initial_state 不存在，跳过元数据注入")
        return

    original = propagator.create_initial_state

    def _patched(
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
        portfolio_context: str = "",
        **kwargs: Any,
    ):
        merged = metadata_context
        if instrument_context:
            merged = f"{merged}\n\n---\n\n{instrument_context}"
        return original(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=merged,
            portfolio_context=portfolio_context,
            **kwargs,
        )

    propagator.create_initial_state = _patched  # type: ignore[method-assign]
    logger.info("[TA context] 已注入 %s 字符标的元数据到 instrument_context", len(metadata_context))


def patch_past_context(graph: Any, metadata_context: str) -> None:
    """兼容旧调用方的别名；新代码应使用 :func:`patch_instrument_context`。"""
    patch_instrument_context(graph, metadata_context)
