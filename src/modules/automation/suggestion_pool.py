"""Suggestion pool: collects items from every agent (recommendation mode only)."""

import logging
from datetime import datetime, timedelta
from typing import Optional
from datetime import timezone
from sqlalchemy import and_, func, or_

from src.platform.compliance import Feature, is_feature_enabled
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import StockSuggestion
from src.platform.scheduling.timezone import utc_now, to_iso_with_tz
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().split())


def _dedupe_window_minutes(agent_name: str) -> int:
    # Default: keep the suggestion list stable and avoid repeated rows.
    # Intraday runs frequently; other agents run a few times a day.
    if agent_name == "intraday_monitor":
        return 30
    if agent_name == "news_digest":
        return 60
    return 180


# Validity per agent (hours)
AGENT_EXPIRY_HOURS = {
    "premarket_outlook": 12,  # pre-market items last the day (about 12 hours)
    "intraday_monitor": 6,  # intraday items last 6 hours
    "daily_report": 16,  # close-report items last overnight (to the next open, about 16 hours)
    "news_digest": 12,  # news digest items last half a day
}

# Agent display names
AGENT_LABELS = {
    "premarket_outlook": "Pre-market outlook",
    "intraday_monitor": "Intraday monitor",
    "daily_report": "Daily close report",
    "news_digest": "News digest",
}

def save_suggestion(
    stock_symbol: str,
    stock_name: str,
    action: str,
    action_label: str,
    agent_name: str,
    signal: str = "",
    reason: str = "",
    agent_label: str = "",
    expires_hours: Optional[int] = None,
    prompt_context: str = "",
    ai_response: str = "",
    stock_market: str = "IN",
    meta: dict | None = None,
) -> bool:
    """
    Save an agent item to the pool.

    Args:
        stock_symbol: stock symbol
        stock_name: stock name
        action: action type (buy/add/reduce/sell/hold/watch/alert/avoid)
        action_label: action display label
        agent_name: agent name
        signal: signal
        reason: reason
        agent_label: agent display name (optional; inferred automatically)
        expires_hours: expiry in hours; the default config when omitted
        prompt_context: prompt context summary
        ai_response: raw AI response

    Returns:
        Whether it was saved
    """
    # Research-only: AI buy/sell/hold suggestions are never stored or shown (ADR-004).
    if not is_feature_enabled(Feature.SUGGESTION_POOL):
        return False
    db = SessionLocal()
    try:
        market = (stock_market or "IN").strip().upper() or "IN"

        # Expiry time (UTC)
        if expires_hours is None:
            expires_hours = AGENT_EXPIRY_HOURS.get(agent_name, 8)

        now = utc_now()
        expires_at = now + timedelta(hours=expires_hours)

        # Agent label
        if not agent_label:
            agent_label = AGENT_LABELS.get(agent_name, agent_name)

        # Dedupe: if the latest suggestion from the same agent is essentially the same,
        # do not create a new row. This prevents flip-flopping AI items in the UI.
        try:
            latest = (
                db.query(StockSuggestion)
                .filter(
                    StockSuggestion.stock_symbol == stock_symbol,
                    StockSuggestion.stock_market == market,
                    StockSuggestion.agent_name == agent_name,
                )
                .order_by(StockSuggestion.created_at.desc(), StockSuggestion.id.desc())
                .first()
            )

            if latest and latest.created_at:
                latest_created = latest.created_at
                if latest_created.tzinfo is None:
                    latest_created = latest_created.replace(tzinfo=timezone.utc)

                window = timedelta(minutes=_dedupe_window_minutes(agent_name))
                same_key = (
                    _norm_text(latest.action) == _norm_text(action)
                    and _norm_text(latest.action_label) == _norm_text(action_label)
                    and _norm_text(latest.signal or "") == _norm_text(signal)
                )

                if same_key and (now - latest_created) <= window:
                    # Extend expiry (keep the first message to avoid churn).
                    if not latest.expires_at or latest.expires_at < expires_at:
                        latest.expires_at = expires_at
                    if not (latest.stock_name or "") and stock_name:
                        latest.stock_name = stock_name
                    db.commit()
                    logger.info(
                        f"Item deduplicated: {stock_symbol} {action_label} (source: {agent_label})"
                    )
                    return True

                # Stability: avoid flip-flopping to a less severe action within a short window.
                try:
                    action_rank = {
                        "alert": 4,
                        "avoid": 4,
                        "sell": 4,
                        "reduce": 3,
                        "buy": 2,
                        "add": 2,
                        "hold": 1,
                        "watch": 0,
                    }
                    old_r = action_rank.get((latest.action or "").strip(), 0)
                    new_r = action_rank.get((action or "").strip(), 0)
                    change_window = timedelta(
                        minutes=_dedupe_window_minutes(agent_name)
                    )
                    if (now - latest_created) <= change_window and new_r < old_r:
                        # Keep the previous (more severe) action; extend expiry.
                        if not latest.expires_at or latest.expires_at < expires_at:
                            latest.expires_at = expires_at
                        if not (latest.stock_name or "") and stock_name:
                            latest.stock_name = stock_name
                        db.commit()
                        logger.info(
                            f"Item kept stable: {stock_symbol} new item is weaker ({action_label}); keeping the previous one ({latest.action_label})"
                        )
                        return True
                except Exception:
                    db.rollback()
        except Exception:
            # Best-effort only; never block saving.
            db.rollback()

        # Create the new item
        suggestion = StockSuggestion(
            stock_symbol=stock_symbol,
            stock_market=market,
            stock_name=stock_name,
            action=action,
            action_label=action_label,
            signal=signal,
            reason=reason,
            agent_name=agent_name,
            agent_label=agent_label,
            expires_at=expires_at,
            prompt_context=prompt_context[:2000] if prompt_context else "",  # cap the length
            ai_response=ai_response[:2000] if ai_response else "",  # cap the length
            meta=to_jsonable(meta or {}),
        )
        db.add(suggestion)
        db.commit()

        logger.info(f"Saved item: {stock_symbol} {action_label} (source: {agent_label})")
        return True

    except Exception as e:
        logger.error(f"Failed to save item: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_suggestions_for_stock(
    stock_symbol: str,
    stock_market: str | None = None,
    include_expired: bool = False,
    limit: int = 10,
) -> list[dict]:
    """
    Items for one stock.

    Args:
        stock_symbol: stock symbol
        include_expired: include expired items
        limit: maximum number of rows

    Returns:
        Items, newest first
    """
    db = SessionLocal()
    try:
        query = db.query(StockSuggestion).filter(StockSuggestion.stock_symbol == stock_symbol)
        if stock_market:
            query = query.filter(
                StockSuggestion.stock_market == (stock_market or "IN").strip().upper()
            )

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None)
                | (StockSuggestion.expires_at > now)
            )

        suggestions = (
            query.order_by(StockSuggestion.created_at.desc()).limit(limit).all()
        )

        return [_to_dict(s, now) for s in suggestions]

    finally:
        db.close()


def get_latest_suggestions(
    stock_symbols: Optional[list[str]] = None,
    stock_keys: Optional[list[tuple[str, str]]] = None,
    include_expired: bool = False,
) -> dict[str, dict]:
    """
    Latest item for every stock (only the newest one per stock).

    Args:
        stock_symbols: stock symbols; None means all
        include_expired: include expired items

    Returns:
        {symbol: suggestion_dict}
    """
    db = SessionLocal()
    try:
        subquery = (
            db.query(
                StockSuggestion.stock_symbol,
                StockSuggestion.stock_market,
                func.max(StockSuggestion.id).label("max_id"),
            )
            .group_by(StockSuggestion.stock_symbol, StockSuggestion.stock_market)
            .subquery()
        )

        query = db.query(StockSuggestion).join(
            subquery,
            and_(
                StockSuggestion.stock_symbol == subquery.c.stock_symbol,
                StockSuggestion.stock_market == subquery.c.stock_market,
                StockSuggestion.id == subquery.c.max_id,
            ),
        )

        if stock_keys:
            norm_keys = []
            for symbol, market in stock_keys:
                sym = (symbol or "").strip().upper()
                mkt = (market or "IN").strip().upper()
                if sym:
                    norm_keys.append((sym, mkt))
            if norm_keys:
                query = query.filter(
                    or_(
                        *[
                            and_(
                                StockSuggestion.stock_symbol == sym,
                                StockSuggestion.stock_market == mkt,
                            )
                            for sym, mkt in norm_keys
                        ]
                    )
                )
            else:
                return {}
        elif stock_symbols:
            query = query.filter(StockSuggestion.stock_symbol.in_(stock_symbols))

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None)
                | (StockSuggestion.expires_at > now)
            )

        suggestions = query.all()

        result: dict[str, dict] = {}
        for s in suggestions:
            key = f"{(s.stock_market or 'IN').upper()}:{s.stock_symbol}"
            result[key] = _to_dict(s, now)
        return result

    finally:
        db.close()


def _to_dict(suggestion: StockSuggestion, now: Optional[datetime] = None) -> dict:
    """Convert a StockSuggestion to a dict (times as ISO strings with a time zone)."""
    if now is None:
        now = utc_now()

    is_expired = False
    if suggestion.expires_at:
        # Compare in UTC on both sides
        expires_utc = suggestion.expires_at
        if expires_utc.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            expires_utc = expires_utc.replace(tzinfo=timezone.utc)
        is_expired = expires_utc < now

    # Times as ISO strings with a time zone
    created_at_str = None
    if suggestion.created_at:
        created_at = suggestion.created_at
        if created_at.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_str = to_iso_with_tz(created_at)

    expires_at_str = None
    if suggestion.expires_at:
        expires_at = suggestion.expires_at
        if expires_at.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at_str = to_iso_with_tz(expires_at)

    return {
        "id": suggestion.id,
        "stock_symbol": suggestion.stock_symbol,
        "stock_market": suggestion.stock_market or "IN",
        "stock_name": suggestion.stock_name,
        "action": suggestion.action,
        "action_label": suggestion.action_label,
        "signal": suggestion.signal,
        "reason": suggestion.reason,
        "agent_name": suggestion.agent_name,
        "agent_label": suggestion.agent_label,
        "created_at": created_at_str,
        "expires_at": expires_at_str,
        "is_expired": is_expired,
        "prompt_context": suggestion.prompt_context or "",
        "ai_response": suggestion.ai_response or "",
        "meta": suggestion.meta or {},
        "should_alert": (suggestion.action or "")
        in ("alert", "avoid", "sell", "reduce"),
    }


def cleanup_expired_suggestions(days: int = 7) -> int:
    """
    Delete expired items.

    Args:
        days: delete rows older than this many days

    Returns:
        Number of rows deleted
    """
    db = SessionLocal()
    try:
        cutoff = utc_now() - timedelta(days=days)
        result = (
            db.query(StockSuggestion)
            .filter(StockSuggestion.created_at < cutoff)
            .delete()
        )
        db.commit()
        logger.info(f"Deleted {result} expired items")
        return result
    except Exception as e:
        logger.error(f"Failed to delete expired items: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
