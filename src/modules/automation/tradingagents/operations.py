"""Intraday sharp rise/fall link: automatically start a TradingAgents deep research run.

Design:
- after intraday_monitor finishes analysing one stock, it calls `try_auto_trigger`
- trigger condition (MVP): |change_pct| >= threshold (default 5%, read from the tradingagents config)
- guard rails: cooldown (default 24h) + monthly budget (reuses cost_tracker)
- off by default (enabled=false); must be switched on explicitly under "Deep config" in the Agents list

The second half of this file holds history backfill and past-decision comparison; neither runs in the TradingAgents main graph.

Why not reuse BaseAgent.run:
- intraday_monitor runs many stocks in one loop and any of them may trigger, so this must be fire-and-forget
- the triggered TA run goes through trigger_agent_for_stock's own async queue so it doesn't block the main loop
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AgentConfig,
    AnalysisHistory,
    StockSuggestion,
)

logger = logging.getLogger(__name__)

# These are peripheral operations entry points used by the API and the intraday monitor; they don't run in the main graph.
__all__ = [
    "backfill_tradingagents_suggestions",
    "build_history_comparison",
    "fire_and_forget_trigger",
    "should_auto_trigger",
    "try_auto_trigger",
]

DEFAULT_CHANGE_PCT_THRESHOLD = 5.0
DEFAULT_COOLDOWN_HOURS = 24


def _read_auto_trigger_config(db: Session) -> dict | None:
    """Read the auto_trigger config from AgentConfig.raw_config.

    Returns:
        {
            "enabled": bool,
            "change_pct_threshold": float,
            "cooldown_hours": int,
        } or None (not configured / not enabled)
    """
    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return None
    raw = agent.raw_config or {}
    auto = raw.get("auto_trigger") or {}
    if not auto.get("enabled"):
        return None
    return {
        "enabled": True,
        "change_pct_threshold": float(auto.get("change_pct_threshold") or DEFAULT_CHANGE_PCT_THRESHOLD),
        "cooldown_hours": int(auto.get("cooldown_hours") or DEFAULT_COOLDOWN_HOURS),
    }


def _within_cooldown(db: Session, stock_symbol: str, cooldown_hours: int) -> bool:
    """Whether a TA run was already started for this stock in the last N hours (from any source)."""
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
    recent = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.created_at >= cutoff,
        )
        .first()
    )
    return recent is not None


def _budget_allows(db: Session) -> bool:
    """Whether the monthly budget has room left. The budget is read from tradingagents raw_config.monthly_budget_usd."""
    try:
        from src.modules.automation.tradingagents.observability import check_budget
    except ImportError:
        return True

    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return True
    raw = agent.raw_config or {}
    budget = float(raw.get("monthly_budget_usd") or 0.0)
    if budget <= 0:
        return True  # no limit set = unlimited

    try:
        status = check_budget(budget)
        return not status.get("exceeded", False)
    except Exception as e:
        logger.warning(f"[auto_trigger] budget check failed; allowing: {e}")
        return True


def should_auto_trigger(
    stock_symbol: str,
    change_pct: float | None,
) -> tuple[bool, str]:
    """Decide whether to start a TA deep research run.

    Returns:
        (should_trigger, reason)
    """
    if change_pct is None:
        return False, "No change % data"

    db = SessionLocal()
    try:
        cfg = _read_auto_trigger_config(db)
        if not cfg:
            return False, "auto_trigger is off"

        if abs(change_pct) < cfg["change_pct_threshold"]:
            return False, f"Change {change_pct:+.2f}% is below the threshold {cfg['change_pct_threshold']}%"

        if _within_cooldown(db, stock_symbol, cfg["cooldown_hours"]):
            return False, f"Cooling down (already triggered in the last {cfg['cooldown_hours']}h)"

        if not _budget_allows(db):
            return False, "Monthly budget used up"

        return True, f"Change {change_pct:+.2f}% reached the threshold {cfg['change_pct_threshold']}%"
    finally:
        db.close()


def fire_and_forget_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """Start a TA deep research run asynchronously without blocking the caller.

    Args:
        stock: an object with at least symbol/name/market (StockData or ORM Stock)
        source_agent: name of the triggering agent (for logs/trace_id)

    Returns:
        the trace_id, or None (trigger failed)
    """
    import time as _time

    try:
        from server import trigger_agent_for_stock
    except ImportError:
        logger.warning("[auto_trigger] server.trigger_agent_for_stock unavailable; skipping")
        return None

    symbol = getattr(stock, "symbol", None)
    if not symbol:
        return None

    trace_id = f"auto-{source_agent}-{symbol}-{int(_time.time() * 1000)}"

    async def _run():
        try:
            await trigger_agent_for_stock(
                "tradingagents",
                stock,
                stock_agent_id=None,
                bypass_throttle=True,
                bypass_market_hours=True,
                suppress_notify=False,
                trace_id=trace_id,
                force_refresh=False,
            )
            logger.info(f"[auto_trigger] TA linked run finished - {symbol} (trace={trace_id})")
        except Exception:
            logger.exception(f"[auto_trigger] TA linked run failed - {symbol}")

    try:
        # Schedule on the current event loop if there is one; otherwise fall back to a new thread
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_run())
        else:
            import threading
            t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
            t.start()
    except RuntimeError:
        import threading
        t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
        t.start()

    return trace_id


def try_auto_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """Combined call: decide + trigger.

    Called after intraday_monitor.analyze finishes. Returns the trace_id or None.
    """
    symbol = getattr(stock, "symbol", "") or ""
    change_pct = getattr(stock, "change_pct", None)

    ok, reason = should_auto_trigger(symbol, change_pct)
    if not ok:
        logger.debug(f"[auto_trigger] not triggering {symbol}: {reason}")
        return None

    logger.info(f"[auto_trigger] starting TA deep research - {symbol} ({reason})")
    return fire_and_forget_trigger(stock, source_agent)


# ============================================================================
# Suggestion backfill
# ============================================================================

def backfill_tradingagents_suggestions(days: int = 7) -> dict:
    """Backfill the tradingagents rows from the last N days of analysis_history into stock_suggestions.

    Returns:
        {"checked": int, "written": int, "skipped": int}
    """
    from src.modules.automation.suggestion_pool import save_suggestion

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    checked = written = skipped = 0
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .all()
        )

        for r in records:
            checked += 1
            raw = r.raw_data or {}
            sug = raw.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "Hold"
            confidence = sug.get("confidence")

            # Is it already in stock_suggestions (same stock + same agent + same action + last 24h)?
            # Simplified: just try to save; save_suggestion deduplicates
            existing = (
                db.query(StockSuggestion)
                .filter(
                    StockSuggestion.stock_symbol == r.stock_symbol,
                    StockSuggestion.agent_name == "tradingagents",
                    StockSuggestion.action == action,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            confidence_text = (
                f" (confidence {confidence:.1f}/10)"
                if isinstance(confidence, (int, float))
                else ""
            )

            symbol = r.stock_symbol
            market = "IN"  # India is the only market

            # Stock name from the AnalysisHistory record (if present)
            stock_name = ""
            try:
                from src.platform.persistence.models import Stock
                stk = db.query(Stock).filter(Stock.symbol == symbol).first()
                if stk:
                    stock_name = stk.name or ""
                    market = stk.market or market
            except Exception:
                pass

            ok = save_suggestion(
                stock_symbol=symbol,
                stock_name=stock_name or symbol,
                stock_market=market,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name="tradingagents",
                agent_label="TradingAgents deep research",
                signal=(sug.get("signal") or "")[:500],
                reason=(sug.get("reason") or "")[:1000],
                expires_hours=24,
                ai_response=(r.content or "")[:2000],
                meta={
                    "cost_usd": raw.get("cost_usd", 0),
                    "decision": raw.get("decision", "HOLD"),
                    "confidence": confidence,
                    "backfilled_at": str(date.today()),
                },
            )
            if ok:
                written += 1
            else:
                skipped += 1

        logger.info(
            f"[TA backfill] checked {checked} history rows, wrote {written} items, skipped {skipped}"
        )
        return {"checked": checked, "written": written, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[TA backfill] failed; skipping: {e}")
        return {"checked": checked, "written": written, "skipped": skipped, "error": str(e)}
    finally:
        db.close()



# ============================================================================
# History comparison
# ============================================================================

def _resolve_market(market: str) -> MarketCode:
    return MarketCode.IN  # India is the only market


def _classify_hit(action: str, ret_pct: float | None) -> bool | None:
    """Whether a decision was a "hit", from the action and the later return."""
    if ret_pct is None:
        return None
    if action == "buy":
        return ret_pct > 0
    if action == "sell":
        return ret_pct < 0
    if action == "hold":
        return abs(ret_pct) < 2.0
    return None


def _find_close_on_or_after(klines_by_date: dict[str, float], target: str) -> tuple[str, float] | None:
    """Close of the nearest trading day on or after target. Looks back at most 7 days (holidays)."""
    base = date.fromisoformat(target)
    for offset in range(8):
        d = (base + timedelta(days=offset)).isoformat()
        if d in klines_by_date:
            return d, klines_by_date[d]
    return None


def _find_close_after_n_trading_days(
    sorted_dates: list[str],
    base_date: str,
    n: int,
    klines_by_date: dict[str, float],
) -> float | None:
    """Close N trading days after base_date. base_date must already be a trading day."""
    try:
        idx = sorted_dates.index(base_date)
    except ValueError:
        return None
    target_idx = idx + n
    if target_idx >= len(sorted_dates):
        return None
    return klines_by_date[sorted_dates[target_idx]]


def build_history_comparison(
    stock_symbol: str,
    market: str = "IN",
    days: int = 90,
) -> dict:
    """Build the TradingAgents past-decision comparison for a stock.

    Args:
        stock_symbol: stock symbol
        market: CN / US / HK
        days: how many days of TA history to look back

    Returns:
        {
            "items": [...],     # by analysis_date, newest first
            "stats": {...},     # hit rate + average return
        }
    """
    symbol = (stock_symbol or "").strip()
    if not symbol:
        return {"items": [], "stats": _empty_stats()}

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.stock_symbol == symbol,
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .order_by(AnalysisHistory.analysis_date.desc())
            .all()
        )
    except Exception as e:
        logger.warning(f"[TA history] query failed: {e}")
        db.close()
        return {"items": [], "stats": _empty_stats()}
    finally:
        db.close()

    if not records:
        return {"items": [], "stats": _empty_stats()}

    # Fetch past K-lines (look-back days + a 30-day buffer so the earliest decision still gets a 20-day return)
    try:
        collector = KlineCollector(_resolve_market(market))
        klines = collector.get_klines(symbol, days=days + 40)
    except Exception as e:
        logger.warning(f"[TA history] K-line fetch failed: {e}")
        klines = []

    klines_by_date = {k.date: k.close for k in klines}
    sorted_dates = sorted(klines_by_date.keys())

    items: list[dict] = []
    for r in records:
        raw = r.raw_data or {}
        sug = raw.get("suggestion") or {}
        action = (sug.get("action") or "hold").lower()
        confidence = sug.get("confidence")
        cost_usd = raw.get("cost_usd")
        # The analysis price prefers the live price saved at analysis time (shown at once); K-line close is the fallback
        stored_price = raw.get("price_at_analysis")
        stored_price = round(float(stored_price), 2) if isinstance(stored_price, (int, float)) else None

        base = _find_close_on_or_after(klines_by_date, r.analysis_date)
        if base is None:
            items.append({
                "trace_id": "",
                "analysis_date": r.analysis_date,
                "action": action,
                "action_label": sug.get("action_label") or _action_to_label(action),
                "confidence": confidence,
                "cost_usd": cost_usd,
                "price_at_analysis": stored_price,
                "return_1d_pct": None,
                "return_5d_pct": None,
                "return_20d_pct": None,
                "hit_20d": None,
            })
            continue

        base_date, base_close = base
        ret = {}
        for n_days in (1, 5, 20):
            close_n = _find_close_after_n_trading_days(sorted_dates, base_date, n_days, klines_by_date)
            ret[n_days] = (
                round((close_n - base_close) / base_close * 100, 2) if close_n is not None else None
            )

        items.append({
            "trace_id": "",
            "analysis_date": r.analysis_date,
            "action": action,
            "action_label": sug.get("action_label") or _action_to_label(action),
            "confidence": confidence,
            "cost_usd": cost_usd,
            "price_at_analysis": stored_price if stored_price is not None else round(base_close, 2),
            "return_1d_pct": ret[1],
            "return_5d_pct": ret[5],
            "return_20d_pct": ret[20],
            "hit_20d": _classify_hit(action, ret[20]),
        })

    return {"items": items, "stats": _compute_stats(items)}


def _action_to_label(action: str) -> str:
    return {"buy": "Buy", "sell": "Sell", "hold": "Hold"}.get(action, action)


def _empty_stats() -> dict:
    return {
        "total": 0,
        "buy_count": 0,
        "sell_count": 0,
        "hold_count": 0,
        "buy_hit_rate": None,
        "sell_hit_rate": None,
        "hold_hit_rate": None,
        "overall_hit_rate": None,
        "avg_return_20d_pct": None,
    }


def _compute_stats(items: list[dict]) -> dict:
    """Statistics: only items that already have a 20-day return."""
    scored = [x for x in items if x.get("return_20d_pct") is not None]
    if not scored:
        return {**_empty_stats(), "total": len(items)}

    def _rate(action: str) -> float | None:
        subset = [x for x in scored if x["action"] == action]
        if not subset:
            return None
        hits = sum(1 for x in subset if x.get("hit_20d"))
        return round(hits / len(subset), 3)

    overall_hits = sum(1 for x in scored if x.get("hit_20d"))
    avg_return = round(sum(x["return_20d_pct"] for x in scored) / len(scored), 2)

    return {
        "total": len(items),
        "buy_count": sum(1 for x in items if x["action"] == "buy"),
        "sell_count": sum(1 for x in items if x["action"] == "sell"),
        "hold_count": sum(1 for x in items if x["action"] == "hold"),
        "buy_hit_rate": _rate("buy"),
        "sell_hit_rate": _rate("sell"),
        "hold_hit_rate": _rate("hold"),
        "overall_hit_rate": round(overall_hits / len(scored), 3),
        "avg_return_20d_pct": avg_return,
    }
