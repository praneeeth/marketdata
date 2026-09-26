"""PanWatch business adapters for the framework-free PanAgent runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from pan_agent import (
    RunRequest,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from sqlalchemy.orm import Session

from src.modules.market.price_alert_service import (
    compact_alert_rule,
    create_alert_rule,
    delete_alert_rule,
    get_alert_rule,
    list_alert_rules,
    update_alert_rule,
)
from src.modules.market.brokers import get_broker_manager
from src.modules.portfolio import build_portfolio_service
from src.modules.strategy.strategy_engine import list_strategy_signals
from src.platform.compliance import Feature, is_feature_enabled
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.marketdata_client import (
    md_news,
    md_quote_rows,
)
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.persistence.models import Stock
from src.platform.runtime.config import Settings


def _symbol_and_market(arguments: dict[str, Any]) -> tuple[str, MarketCode] | None:
    """Validate the small symbol contract shared by all market tools."""
    symbol = str(arguments.get("symbol") or "").strip().upper()
    try:
        market = MarketCode(str(arguments.get("market") or "IN").strip().upper())
    except ValueError:
        return None
    return (symbol, market) if symbol else None


def _failure_for_symbol(arguments: dict[str, Any]) -> ToolResult:
    if not str(arguments.get("symbol") or "").strip():
        return ToolResult.failure(
            summary="Please give a stock symbol.", error_code="symbol_required"
        )
    return ToolResult.failure(summary="Unsupported market code.", error_code="market_invalid")


def _optional_market(arguments: dict[str, Any]) -> MarketCode | None:
    """Parse an optional market filter without forcing queries to one market."""
    raw = str(arguments.get("market") or "").strip().upper()
    if not raw:
        return None
    try:
        return MarketCode(raw)
    except ValueError:
        return None


def _alert_condition_summary(item: dict[str, Any]) -> str:
    direction = "≥" if item.get("direction") == "above" else "≤"
    target = item.get("target_price")
    if target is None:
        return "unknown condition"
    return f"price {direction} {target:g}"


def _published_at(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _market_argument(arguments: dict[str, Any]) -> MarketCode | None:
    raw = str(arguments.get("market") or "IN").strip().upper()
    try:
        return MarketCode(raw)
    except ValueError:
        return None


def _format_candidate_price(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _compact_research_candidate(item: dict[str, Any]) -> dict[str, Any]:
    entry_low = item.get("entry_low")
    entry_high = item.get("entry_high")
    if entry_low is not None or entry_high is not None:
        entry_range = f"{_format_candidate_price(entry_low) or '--'} ~ {_format_candidate_price(entry_high) or '--'}"
    else:
        entry_range = ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
    quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return {
        "symbol": str(item.get("stock_symbol") or ""),
        "market": str(item.get("stock_market") or "IN"),
        "name": str(item.get("stock_name") or item.get("stock_symbol") or ""),
        "score": item.get("rank_score", item.get("score")),
        "action": item.get("action_label") or item.get("action") or "Watch",
        "risk": item.get("risk_level_label") or item.get("risk_level") or "unknown",
        "source": item.get("source_pool_label") or item.get("source_pool") or "unknown",
        "signal": item.get("signal") or "",
        "reason": item.get("reason") or "",
        "entry_range": entry_range,
        "target_price": item.get("target_price"),
        "stop_loss": item.get("stop_loss"),
        "invalidation": item.get("invalidation") or "",
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
    }


def build_panwatch_tool_registry(session: Session) -> ToolRegistry:
    """Register the host-owned market and portfolio tools for an assistant run."""
    registry = ToolRegistry()
    portfolio_service = build_portfolio_service(session)

    async def get_portfolio(_request: RunRequest, _arguments: dict) -> ToolResult:
        summary = portfolio_service.build_assistant_summary() or "The user has no positions."
        return ToolResult.success(
            summary=summary,
            data={"has_positions": summary != "The user has no positions."},
            sources=[{"name": "PanWatch positions"}],
            observed_at=datetime.now(UTC),
        )

    async def find_research_candidates(_request: RunRequest, arguments: dict) -> ToolResult:
        market = str(arguments.get("market") or "").strip().upper()
        holding = str(arguments.get("holding") or "unheld").strip().lower()
        risk_level = str(arguments.get("risk_level") or "").strip().lower()
        try:
            min_score = float(arguments.get("min_score", 70))
            limit = int(arguments.get("limit", 5))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="Invalid screening parameters.",
                error_code="candidate_filter_invalid",
            )
        if (
            (market and market != "IN")
            or holding not in {"all", "held", "unheld"}
            or (risk_level and risk_level not in {"all", "low", "medium", "high"})
            or not 0 <= min_score <= 100
            or not 1 <= limit <= 10
        ):
            return ToolResult.failure(
                summary="Invalid screening parameters.",
                error_code="candidate_filter_invalid",
            )

        try:
            result = await asyncio.to_thread(
                list_strategy_signals,
                market=market,
                status="active",
                min_score=min_score,
                limit=limit,
                source_pool="all",
                holding=holding,
                risk_level=risk_level,
                include_payload=True,
            )
        except Exception:  # noqa: BLE001 - provider/database failures become controlled tool results
            return ToolResult.failure(
                summary="Screening data is unavailable right now.",
                error_code="candidate_data_unavailable",
            )

        items = [_compact_research_candidate(item) for item in result.get("items", [])]
        names = ", ".join(item["name"] for item in items[:3])
        summary = (
            f"Found {len(items)} research candidates: {names}."
            if items
            else "No research candidates match."
        )
        return ToolResult.success(
            summary=summary,
            data={
                "snapshot_date": result.get("snapshot_date") or "",
                "count": len(items),
                "items": items,
            },
            sources=[{"name": "PanWatch screening signals"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_quote(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - provider failures become controlled tool results
            return ToolResult.failure(
                summary="Quote data is unavailable right now.", error_code="quote_unavailable"
            )
        quote = next(
            (row for row in rows if str(row.get("symbol") or "") == symbol), None
        )
        if quote is None:
            return ToolResult.failure(
                summary=f"No quote found for {market.value}:{symbol}.",
                error_code="quote_unavailable",
            )
        data = {
            key: quote.get(key)
            for key in (
                "symbol",
                "name",
                "market",
                "current_price",
                "change_pct",
                "change_amount",
                "prev_close",
                "open_price",
                "high_price",
                "low_price",
                "volume",
                "turnover",
                "turnover_rate",
                "pe_ratio",
                "total_market_value",
                "circulating_market_value",
            )
        }
        name = data.get("name") or symbol
        return ToolResult.success(
            summary=(
                f"{name} ({market.value}:{symbol}) last price {data.get('current_price')}, "
                f"change {data.get('change_pct')}%."
            ),
            data=data,
            sources=[{"name": "PanWatch quotes"}],
            observed_at=datetime.now(UTC),
        )

    async def get_kline_summary(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            summary = await asyncio.to_thread(
                KlineCollector(market).get_kline_summary, symbol
            )
        except Exception:  # noqa: BLE001 - source issues must not abort an agent run
            return ToolResult.failure(
                summary="K-line data is unavailable right now.", error_code="kline_unavailable"
            )
        if not isinstance(summary, dict) or not summary:
            return ToolResult.failure(
                summary=f"No K-line summary found for {market.value}:{symbol}.",
                error_code="kline_unavailable",
            )
        return ToolResult.success(
            summary=f"K-line summary for {market.value}:{symbol}: {summary}",
            data=summary,
            sources=[{"name": "PanWatch K-lines"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_news(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            limit = max(1, min(int(arguments.get("limit") or 5), 10))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="The news limit must be a number.", error_code="limit_invalid"
            )
        try:
            articles = await asyncio.to_thread(
                md_news, [symbol], since_hours=168, names=None
            )
        except Exception:  # noqa: BLE001 - data-source failures stay within the tool result
            return ToolResult.failure(
                summary="News data is unavailable right now.", error_code="news_unavailable"
            )
        items = [
            {
                "title": str(getattr(article, "title", "") or ""),
                "source": str(getattr(article, "source", "") or ""),
                "published_at": _published_at(getattr(article, "publish_time", None)),
                "url": str(getattr(article, "url", "") or ""),
                "importance": int(getattr(article, "importance", 0) or 0),
            }
            for article in articles[:limit]
        ]
        return ToolResult.success(
            summary=f"{len(items)} news items for {market.value}:{symbol} in the last 7 days.",
            data={"symbol": symbol, "market": market.value, "items": items},
            sources=[{"name": "PanWatch news"}],
            observed_at=datetime.now(UTC),
        )

    async def search_stocks_tool(_request: RunRequest, arguments: dict) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult.failure(
                summary="Please give a stock symbol or name.", error_code="search_query_required"
            )
        raw_market = str(arguments.get("market") or "").strip().upper()
        if raw_market:
            try:
                market = MarketCode(raw_market)
            except ValueError:
                return ToolResult.failure(
                    summary="Unsupported market code.", error_code="market_invalid"
                )
            market_value = market.value
        else:
            market_value = ""
        try:
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="The search limit must be a number.", error_code="limit_invalid"
            )
        try:
            # NSE/BSE equities and indices from the user's own broker instrument list.
            items = await asyncio.to_thread(
                get_broker_manager().search_instruments, session, query, limit
            )
        except Exception:  # noqa: BLE001 - search providers become controlled results
            return ToolResult.failure(
                summary="Stock search is unavailable right now.", error_code="stock_search_unavailable"
            )
        data = [
            {
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or ""),
                "market": str(item.get("market") or ""),
            }
            for item in items
        ]
        return ToolResult.success(
            summary=(f"Found {len(data)} stocks." if data else "No matching stocks found."),
            data={"query": query, "count": len(data), "items": data},
            sources=[{"name": "Broker instrument list"}],
            observed_at=datetime.now(UTC),
        )

    async def get_market_status(_request: RunRequest, _arguments: dict) -> ToolResult:
        markets = []
        for code, definition in MARKETS.items():
            try:
                is_trading = definition.is_trading_time()
            except Exception:  # noqa: BLE001 - calendar failures stay in the result
                is_trading = None
            sessions = [
                f"{item.start.strftime('%H:%M')}-{item.end.strftime('%H:%M')}"
                for item in definition.sessions
            ]
            markets.append(
                {
                    "market": code.value,
                    "name": definition.name,
                    "timezone": definition.timezone,
                    "status": (
                        "trading"
                        if is_trading is True
                        else "closed"
                        if is_trading is False
                        else "unknown"
                    ),
                    "is_trading": is_trading,
                    "sessions": sessions,
                }
            )
        return ToolResult.success(
            summary="; ".join(
                f"{item['name']}: "
                f"{'open' if item['is_trading'] is True else 'closed' if item['is_trading'] is False else 'status unknown'}"
                for item in markets
            ),
            data={"markets": markets},
            sources=[{"name": "PanWatch market calendar"}],
            observed_at=datetime.now(UTC),
        )


    async def _find_or_register_stock(
        symbol: str, market: MarketCode
    ) -> tuple[Stock | None, bool]:
        """Resolve a stock id for write tools without requiring watchlist setup.

        Price-alert rules reference the local ``stocks`` table, while research
        tools can operate on any symbol returned by the market-data providers.
        A verified quote is enough to create the lightweight stock directory
        record; an unverified symbol remains a controlled ``stock_not_found``
        result and never produces a dangling alert rule.
        """
        stock = (
            session.query(Stock)
            .filter(Stock.symbol == symbol, Stock.market == market.value)
            .first()
        )
        if stock is not None:
            return stock, False

        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - quote failures become a controlled write failure
            return None, False

        def matches(row: dict[str, Any]) -> bool:
            row_symbol = str(row.get("symbol") or "").strip().upper()
            return row_symbol == symbol

        quote = next((row for row in rows if matches(row)), None)
        if quote is None:
            return None, False

        stock = Stock(
            symbol=symbol,
            name=str(quote.get("name") or symbol).strip() or symbol,
            market=market.value,
        )
        session.add(stock)
        # The rule references the newly registered stock by foreign key: flush to get its
        # id; the single commit below still makes the stock and the rule succeed or roll
        # back together.
        session.flush()
        return stock, True

    async def create_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        direction = str(arguments.get("direction") or "").strip().lower()
        if direction not in {"above", "below"}:
            return ToolResult.failure(
                summary="The alert direction must be above or below.",
                error_code="direction_invalid",
            )
        try:
            target_price = float(arguments.get("target_price"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="The alert price must be a number above zero.",
                error_code="target_price_invalid",
            )
        if target_price <= 0:
            return ToolResult.failure(
                summary="The alert price must be above zero.", error_code="target_price_invalid"
            )
        try:
            cooldown_minutes = max(0, int(arguments.get("cooldown_minutes") or 30))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="The cooldown must be a non-negative integer.", error_code="cooldown_invalid"
            )

        stock, stock_registered = await _find_or_register_stock(symbol, market)
        if stock is None:
            return ToolResult.failure(
                summary=f"{market.value}:{symbol} is not in the watchlist; no alert was created.",
                error_code="stock_not_found",
            )
        direction_label = "≥" if direction == "above" else "≤"
        display_price = f"{target_price:g}"
        name = (
            str(arguments.get("name") or "").strip()
            or f"{stock.name} price {direction_label} {display_price}"
        )
        rule = create_alert_rule(
            session,
            stock_id=stock.id,
            name=name,
            enabled=True,
            condition_group={
                "op": "and",
                "items": [
                    {
                        "type": "price",
                        "op": ">=" if direction == "above" else "<=",
                        "value": target_price,
                    }
                ],
            },
            market_hours_mode="trading_only",
            cooldown_minutes=cooldown_minutes,
            max_triggers_per_day=3,
            repeat_mode="repeat",
            notify_channel_ids=[],
        )
        return ToolResult.success(
            summary=(
                f"Created an intraday alert for {stock.name} ({market.value}:{symbol}) when the price is "
                f"{direction_label} {display_price}, with a {cooldown_minutes}-minute cooldown."
            ),
            data={
                "rule_id": rule.id,
                "symbol": symbol,
                "market": market.value,
                "direction": direction,
                "target_price": target_price,
                "stock_registered": stock_registered,
            },
            sources=[{"name": "PanWatch price alerts"}],
            observed_at=datetime.now(UTC),
        )

    async def get_price_alerts(_request: RunRequest, arguments: dict) -> ToolResult:
        """Return compact alert facts so the model can reference a rule ID."""
        symbol = str(arguments.get("symbol") or "").strip().upper() or None
        market = _optional_market(arguments)
        if arguments.get("market") and market is None:
            return ToolResult.failure(
                summary="Unsupported market code.", error_code="market_invalid"
            )
        try:
            limit = max(1, min(int(arguments.get("limit") or 20), 50))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="The limit must be a number.", error_code="limit_invalid"
            )
        enabled = arguments.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return ToolResult.failure(
                summary="enabled must be true or false.", error_code="enabled_invalid"
            )

        rows = list_alert_rules(
            session,
            symbol=symbol,
            market=market.value if market else None,
            enabled=enabled,
            limit=limit,
        )
        items = [compact_alert_rule(row) for row in rows]
        if not items:
            return ToolResult.success(
                summary="No matching price alerts found.",
                data={"count": 0, "items": []},
                sources=[{"name": "PanWatch price alerts"}],
                observed_at=datetime.now(UTC),
            )
        summary = "; ".join(
            f"#{item['rule_id']} {item['stock_name'] or item['symbol']} ({item['market']}:{item['symbol']}, "
            f"{_alert_condition_summary(item)}, {'on' if item['enabled'] else 'off'})"
            for item in items[:5]
        )
        if len(items) > 5:
            summary += f"; and {len(items) - 5} more"
        return ToolResult.success(
            summary=f"Found {len(items)} price alerts: {summary}",
            data={"count": len(items), "items": items},
            sources=[{"name": "PanWatch price alerts"}],
            observed_at=datetime.now(UTC),
        )

    async def update_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Update one rule after the runtime's human approval gate."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="Please give a valid alert ID.", error_code="rule_id_invalid"
            )
        updates = {
            key: arguments[key]
            for key in (
                "name",
                "enabled",
                "direction",
                "target_price",
                "cooldown_minutes",
                "max_triggers_per_day",
                "repeat_mode",
                "market_hours_mode",
                "expire_at",
            )
            if key in arguments
        }
        try:
            rule = update_alert_rule(session, rule_id, updates)
        except LookupError:
            return ToolResult.failure(
                summary=f"Price alert #{rule_id} not found.",
                error_code="price_alert_not_found",
            )
        except ValueError as exc:
            return ToolResult.failure(
                summary=str(exc), error_code="price_alert_invalid"
            )
        item = compact_alert_rule(rule)
        return ToolResult.success(
            summary=(
                f"Updated price alert #{rule_id}: {item['stock_name'] or item['symbol']}, "
                f"{_alert_condition_summary(item)}, {'on' if item['enabled'] else 'off'}."
            ),
            data=item,
            sources=[{"name": "PanWatch price alerts"}],
            observed_at=datetime.now(UTC),
        )

    async def delete_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Delete one rule and its hit history after human approval."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="Please give a valid alert ID.", error_code="rule_id_invalid"
            )
        rule = get_alert_rule(session, rule_id)
        if rule is None:
            return ToolResult.failure(
                summary=f"Price alert #{rule_id} not found.",
                error_code="price_alert_not_found",
            )
        item = compact_alert_rule(rule)
        try:
            delete_alert_rule(session, rule_id)
        except LookupError:
            return ToolResult.failure(
                summary=f"Price alert #{rule_id} not found.",
                error_code="price_alert_not_found",
            )
        return ToolResult.success(
            summary=f"Deleted price alert #{rule_id}: {item['stock_name'] or item['symbol']}.",
            data={"rule_id": rule_id, "deleted": True},
            sources=[{"name": "PanWatch price alerts"}],
            observed_at=datetime.now(UTC),
        )

    registry.register(
        ToolSpec(
            name="get_portfolio",
            title="Get positions",
            description="Summarise the user's real and simulated positions.",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_portfolio,
    )
    registry.register(
        ToolSpec(
            name="get_stock_quote",
            title="Get a live quote",
            description="Latest price, change and intraday data for one stock.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "NSE symbol such as INFY, or BSE:<code>",
                    },
                    "market": {
                        "type": "string",
                        "default": "IN",
                        "description": "Market code (IN)",
                    },
                },
            },
        ),
        get_stock_quote,
    )
    # Research-only: the entry-candidate engine (scores, entry plans) is not exposed.
    if is_feature_enabled(Feature.ENTRY_CANDIDATES):
        registry.register(
            ToolSpec(
                name="find_research_candidates",
                title="Find research candidates",
                description="Latest PanWatch screening signals: candidates worth further research with their scores and risk. Read-only; never refreshes strategies or trades.",
                risk=ToolRisk.READ,
                input_schema={
                    "type": "object",
                    "properties": {
                        "market": {
                            "type": "string",
                            "enum": ["IN"],
                            "description": "Optional market code; omit for all",
                        },
                        "holding": {
                            "type": "string",
                            "enum": ["all", "held", "unheld"],
                            "default": "unheld",
                            "description": "Holding filter; by default only stocks not held",
                        },
                        "risk_level": {
                            "type": "string",
                            "enum": ["all", "low", "medium", "high"],
                            "default": "all",
                            "description": "Optional risk level filter",
                        },
                        "min_score": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                            "default": 70,
                            "description": "Minimum score",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                            "description": "Maximum number of candidates",
                        },
                    },
                },
            ),
            find_research_candidates,
        )
    registry.register(
        ToolSpec(
            name="get_kline_summary",
            title="Analyse the price trend",
            description="Moving averages, momentum and recent K-line indicators for one stock.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "NSE symbol such as INFY, or BSE:<code>",
                    },
                    "market": {
                        "type": "string",
                        "default": "IN",
                        "description": "Market code (IN)",
                    },
                },
            },
        ),
        get_kline_summary,
    )
    registry.register(
        ToolSpec(
            name="get_stock_news",
            title="Search stock news",
            description="The last seven days of news for one stock, summarised.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "NSE symbol such as INFY, or BSE:<code>",
                    },
                    "market": {
                        "type": "string",
                        "default": "IN",
                        "description": "Market code (IN)",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
            },
        ),
        get_stock_news,
    )
    registry.register(
        ToolSpec(
            name="search_stocks",
            title="Search stocks",
            description="Search NSE/BSE stocks and indices by symbol or name (from the user's broker instrument list) to confirm the exact symbol.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Stock symbol or name"},
                    "market": {
                        "type": "string",
                        "enum": ["IN"],
                        "description": "Optional market code; omit for all",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                    },
                },
            },
        ),
        search_stocks_tool,
    )
    registry.register(
        ToolSpec(
            name="get_market_status",
            title="Market status",
            description="Whether NSE/BSE are in session now, and today's trading hours (IST).",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_market_status,
    )
    registry.register(
        ToolSpec(
            name="get_price_alerts",
            title="List price alerts",
            description="The user's price alerts with their ID, stock, condition and on/off state.",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Optional stock symbol; omit for all",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["IN"],
                        "description": "Optional market code",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Optional: only enabled or only disabled alerts",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                },
            },
        ),
        get_price_alerts,
    )
    registry.register(
        ToolSpec(
            name="update_price_alert",
            title="Edit a price alert",
            description="Change an alert's name, price, direction or on/off state. Needs the user's approval.",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "Alert ID from List price alerts",
                    },
                    "name": {"type": "string", "description": "New alert name"},
                    "enabled": {"type": "boolean", "description": "Whether the alert is on"},
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "Trigger direction",
                    },
                    "target_price": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "New alert price",
                    },
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Cooldown in minutes after a trigger",
                    },
                    "max_triggers_per_day": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum triggers per day; 0 means no limit",
                    },
                    "repeat_mode": {
                        "type": "string",
                        "enum": ["once", "repeat"],
                    },
                    "market_hours_mode": {
                        "type": "string",
                        "enum": ["trading_only", "always"],
                    },
                    "expire_at": {
                        "type": ["string", "null"],
                        "description": "ISO-8601 expiry; null clears it",
                    },
                },
            },
        ),
        update_price_alert,
    )
    registry.register(
        ToolSpec(
            name="delete_price_alert",
            title="Delete a price alert",
            description="Delete an alert and its trigger history. Needs the user's approval.",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "Alert ID from List price alerts",
                    }
                },
            },
        ),
        delete_price_alert,
    )
    registry.register(
        ToolSpec(
            name="create_price_alert",
            title="Create a price alert",
            description="Create an intraday price alert for a watchlist stock. Needs the user's approval.",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["symbol", "direction", "target_price"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "NSE symbol such as INFY, or BSE:<code>",
                    },
                    "market": {
                        "type": "string",
                        "default": "IN",
                        "description": "Market code (IN)",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "Whether the price rises to or falls to the alert price",
                    },
                    "target_price": {"type": "number", "exclusiveMinimum": 0},
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                    },
                    "name": {"type": "string", "description": "Optional alert name"},
                },
            },
        ),
        create_price_alert,
    )
    registered = {spec.name for spec in registry.registered_tools()}
    for name in (
        "find_research_candidates",
        "get_kline_summary",
        "update_price_alert",
        "delete_price_alert",
        "create_price_alert",
    ):
        if name in registered:
            registry.set_exposure(name, ToolExposure.DEFERRED)
    return registry
