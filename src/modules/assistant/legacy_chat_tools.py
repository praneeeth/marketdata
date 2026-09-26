"""Shared read-only tools used by the legacy chat endpoint and MCP adapter.

The old ``/api/chat`` route and the MCP transport expose the same assistant
capabilities.  Their schema and dispatch therefore live in this module instead
of either HTTP router, so another module can use the assistant boundary without
depending on a router implementation.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from src.platform.compliance import Feature, is_feature_enabled
from src.modules.portfolio import build_portfolio_service
from src.platform.persistence.models import AnalysisHistory, Stock, StockSuggestion


logger = logging.getLogger(__name__)

# Prefix of every tool failure message; chat_api and the MCP endpoint check for it.
TOOL_ERROR_PREFIX = "Tool error"

_ALL_CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Get the user's real holdings and simulation holdings. Use for questions about holdings (are they healthy, profit and loss, etc.).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "Get a stock's live quote (price, change %, volume, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol such as INFY, or BSE:<code>"},
                    "market": {"type": "string", "description": "Market: IN (NSE/BSE, the only market)", "default": "IN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "Get a stock's technical analysis (trend, MACD, RSI, support, resistance, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol"},
                    "market": {"type": "string", "description": "Market: IN (NSE/BSE, the only market)", "default": "IN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "Get a stock's recent AI items and analysis reports.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol"},
                    "market": {"type": "string", "description": "Market: IN (NSE/BSE, the only market)", "default": "IN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "Get the user's watchlist.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Research-only: the AI suggestion tool is removed from chat and MCP (ADR-004).
CHAT_TOOLS = [
    tool
    for tool in _ALL_CHAT_TOOLS
    if tool["function"]["name"] != "get_stock_suggestions"
    or is_feature_enabled(Feature.SUGGESTION_POOL)
]


def build_watchlist_context(db: Session) -> str:
    """Return the user's watchlist in a text form suitable for a tool result."""
    stocks = db.query(Stock).order_by(Stock.sort_order.asc()).all()
    if not stocks:
        return "The user has no watchlist stocks."
    lines = [f"- {stock.name}({stock.market}:{stock.symbol})" for stock in stocks]
    return "Watchlist:\n" + "\n".join(lines)


def build_stock_context(db: Session, symbol: str, market: str) -> str:
    """Return the latest persisted suggestions and analysis for one stock."""
    parts: list[str] = []
    suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = [
            f"- [{item.agent_label or item.agent_name}] {item.action_label}: {item.signal or item.reason or ''}"
            for item in suggestions
        ]
        parts.append("Recent AI items:\n" + "\n".join(lines))

    histories = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.stock_symbol == symbol)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        history = histories[0]
        parts.append(
            f"Recent analysis ({history.agent_name}, {history.analysis_date}):\n{(history.content or '')[:500]}"
        )
    return "\n\n".join(parts)


def build_portfolio_context(db: Session) -> str:
    """Return the portfolio module's public assistant summary."""
    return build_portfolio_service(db).build_assistant_summary()


async def fetch_realtime_context(symbol: str, market: str) -> str:
    """Return a compact quote summary; failures degrade to an empty context."""
    try:
        from src.platform.marketdata.marketdata_client import md_quote_rows
        from src.platform.marketdata.models import MarketCode

        code = MarketCode.IN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], code.value)
        if not rows:
            return ""
        quote = rows[0]
        return (
            f"Live quote: {quote.get('name', symbol)} ({market}:{symbol}) price "
            f"{quote.get('current_price', '--')}, change {quote.get('change_pct', '--')}%, "
            f"volume {quote.get('volume', '--')}"
        )
    except Exception as exc:  # noqa: BLE001 - a missing quote must not fail chat
        logger.debug("Failed to get the live quote: %s", exc)
        return ""


async def fetch_technical_context(symbol: str, market: str) -> str:
    """Return a compact technical summary; failures degrade to an empty context."""
    try:
        from src.platform.marketdata.collectors.kline_collector import KlineCollector

        summary = await asyncio.to_thread(KlineCollector().get_kline_summary, symbol)
        if not summary or summary.get("error"):
            return ""
        data = summary.get("summary", {})
        return (
            f"Technicals: trend {data.get('trend', '--')}, MACD {data.get('macd_status', '--')}, "
            f"RSI {data.get('rsi_14', '--')}, support {data.get('support_level', '--')}, "
            f"resistance {data.get('resistance_level', '--')}"
        )
    except Exception as exc:  # noqa: BLE001 - a missing indicator must not fail chat
        logger.debug("Failed to get technicals: %s", exc)
        return ""


async def execute_chat_tool(db: Session, name: str, arguments: dict) -> str:
    """Dispatch one declared read-only assistant tool."""
    try:
        if name == "get_portfolio":
            return build_portfolio_context(db) or "The user has no holdings."
        if name == "get_stock_quote":
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "IN")
            return await fetch_realtime_context(symbol, market) or f"Could not get quote data for {market}:{symbol}."
        if name == "get_technical_analysis":
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "IN")
            return await fetch_technical_context(symbol, market) or f"Could not get technical data for {market}:{symbol}."
        if name == "get_stock_suggestions":
            if not is_feature_enabled(Feature.SUGGESTION_POOL):
                return "AI buy/sell suggestions are not available in research-only mode."
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "IN")
            return build_stock_context(db, symbol, market) or f"No AI items for {market}:{symbol}."
        if name == "get_watchlist":
            return build_watchlist_context(db)
        return f"Unknown tool: {name}"
    except Exception as exc:  # noqa: BLE001 - preserve the legacy user-facing error contract
        logger.error("Tool failed %s: %s", name, exc)
        return f"{TOOL_ERROR_PREFIX}: {exc}"
