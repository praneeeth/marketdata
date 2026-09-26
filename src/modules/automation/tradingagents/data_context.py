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

# The single entry point for "business data -> TradingAgents context", so callers don't need the internal render functions.
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
    if av >= 1e7:
        return f"{v / 1e7:.2f} Cr"
    if av >= 1e5:
        return f"{v / 1e5:.2f} L"
    return f"{v:.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.2f}%"


def _fmt_period(p: str) -> str:
    """20260331 -> 2026Q1, 20251231 -> 2025Q4 (annual report)"""
    if len(p) != 8:
        return p
    y, m, d = p[:4], p[4:6], p[6:8]
    q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, m)
    return f"{y}{q}"


def render_fundamentals_summary(data: dict) -> str:
    """Render the fundamentals summary (for get_fundamentals)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No financial data available]"

    lines = ["[Reported financials]"]
    lines.append(f"Reporting periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")

    key_metrics = [
        ("Total revenue", _fmt_num),
        ("Net profit attributable to shareholders", _fmt_num),
        ("Net profit excluding exceptional items", _fmt_num),
        ("Basic EPS", lambda v: f"Rs {v:.2f}" if v is not None else "N/A"),
        ("Gross margin", _fmt_pct),
        ("Return on equity (ROE)", _fmt_pct),
        ("Debt-to-assets ratio", _fmt_pct),
        ("Net operating cash flow", _fmt_num),
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
    """Render the income statement (for get_income_statement)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No income statement data]"
    lines = ["[Income statement]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("Total revenue", _fmt_num),
        ("Cost of revenue", _fmt_num),
        ("Net profit attributable to shareholders", _fmt_num),
        ("Net profit", _fmt_num),
        ("Net profit excluding exceptional items", _fmt_num),
        ("Gross margin", _fmt_pct),
        ("Net profit margin", _fmt_pct),
        ("Operating expense ratio", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_balance_sheet(data: dict) -> str:
    """Render the balance sheet (for get_balance_sheet)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No balance sheet data]"
    lines = ["[Balance sheet]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("Total shareholders' equity (net assets)", _fmt_num),
        ("Book value per share", lambda v: f"Rs {v:.2f}" if v is not None else "N/A"),
        ("Goodwill", _fmt_num),
        ("Debt-to-assets ratio", _fmt_pct),
        ("Return on assets (ROA)", _fmt_pct),
        ("Return on equity (ROE)", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_cashflow(data: dict) -> str:
    """Render the cash flow statement (for get_cashflow)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No cash flow data]"
    lines = ["[Cash flow statement]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("Net operating cash flow", _fmt_num),
        ("Cash flow per share", lambda v: f"Rs {v:.2f}" if v is not None else "N/A"),
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
    """Safely convert a value that may come from the database into a finite float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_tradingagents_portfolio(portfolio: Any):
    """Convert ``PortfolioInfo`` into TradingAgents 0.5.0's ``PortfolioContext``.

    The same ticker across accounts is merged with a quantity-weighted average cost. Returns ``None``
    without an account snapshot, so upstream can tell "no portfolio given" from "portfolio with zero cash and positions".
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
            # TradingAgents 0.5.0 uses positive numbers for long and negative for short; only zero
            # quantities and bad data are filtered here; a short must not be dropped as "no position".
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
        # Weight the cost by absolute quantity: same-direction positions match the old logic, and mixed long/short
        # can't produce a meaningless extreme average when the net quantity is near 0; quantity keeps its net sign.
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
    """Inject the app's instrument metadata into TradingAgents 0.5.0's ``instrument_context``.

    ``past_context`` is upstream's extension point for research memory; putting instrument metadata there would
    muddle the prompt semantics and make later research replays treat this stock's info as past experience. 0.5.0's
    ``Propagator.create_initial_state`` exposes ``instrument_context``, so this entry point wraps it once per
    instance and passes portfolio/future kwargs through unchanged.
    """
    if not metadata_context:
        return

    propagator = getattr(graph, "propagator", None)
    if propagator is None or not hasattr(propagator, "create_initial_state"):
        logger.warning("[TA context] propagator.create_initial_state not found; skipping metadata injection")
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
    logger.info("[TA context] Injected %s characters of instrument metadata into instrument_context", len(metadata_context))


def patch_past_context(graph: Any, metadata_context: str) -> None:
    """Alias kept for old callers; new code should use :func:`patch_instrument_context`."""
    patch_instrument_context(graph, metadata_context)
