"""Simulation notifications: live entry/exit alerts, pre-market plan and end-of-day summary."""

from __future__ import annotations

import logging
from typing import Any

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AppSettings,
    NotifyChannel,
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
    StrategySignalRun,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_KEYS = {
    "pt_notify_enabled": "false",
    "pt_notify_channel_ids": "",
    "pt_notify_realtime": "true",
    "pt_notify_premarket": "true",
    "pt_notify_summary": "true",
}


def _load_config() -> dict[str, str]:
    """Read the pt_notify_* settings from the AppSettings table."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(_CONFIG_KEYS.keys()))
            .all()
        )
        cfg = dict(_CONFIG_KEYS)  # defaults
        for r in rows:
            cfg[r.key] = r.value or _CONFIG_KEYS.get(r.key, "")
        return cfg
    finally:
        db.close()


def _is_enabled() -> bool:
    cfg = _load_config()
    return cfg.get("pt_notify_enabled", "").lower() == "true"


def _is_mode_enabled(mode_key: str) -> bool:
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return False
    return cfg.get(mode_key, "").lower() == "true"


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

def _build_notifier() -> NotifierManager | None:
    """Build a NotifierManager from the config; None when no channel is available."""
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return None

    db = SessionLocal()
    try:
        channel_ids_str = cfg.get("pt_notify_channel_ids", "").strip()
        if channel_ids_str:
            ids = [int(x.strip()) for x in channel_ids_str.split(",") if x.strip().isdigit()]
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.id.in_(ids), NotifyChannel.enabled.is_(True))
                .all()
            )
        else:
            # No channel specified: use the default channels
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.enabled.is_(True), NotifyChannel.is_default.is_(True))
                .all()
            )

        if not channels:
            logger.debug("[Simulation notify] No notification channel available")
            return None

        mgr = NotifierManager()
        for ch in channels:
            mgr.add_channel(ch.type, ch.config or {})
        return mgr
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

EXIT_REASON_LABELS = {
    "stop_loss": "Stop loss",
    "target_price": "Take profit",
    "signal_reversal": "Signal reversal",
    "manual": "Manual close",
}

STRATEGY_NAME_MAP = {
    "trend_follow": "Trend continuation",
    "macd_golden": "MACD golden cross",
    "volume_breakout": "Volume breakout",
    "pullback": "Pullback confirmation",
    "rebound": "Oversold rebound",
    "watchlist_agent": "Agent item",
    "market_scan": "Market scan",
    "momentum": "Momentum",
}


def _strategy_label(code: str) -> str:
    """Strategy code to display name."""
    return STRATEGY_NAME_MAP.get(code, code)


def _stock_display(symbol: str, market: str, name: str = "") -> str:
    """Stock display text with a link; clicking the symbol opens the quote page."""
    from src.modules.administration.stock_link import stock_link_markdown
    label = f"{name} " if name else ""
    return f"{label}({stock_link_markdown(symbol, market)})"


def _format_entry_message(pos: dict, sig: dict | None) -> tuple[str, str]:
    """Format an entry notification; returns (title, body). pos/sig are serialised dicts."""
    name = pos.get("stock_name") or pos["stock_symbol"]
    title = f"[SIMULATION] Simulated entry: {name}"

    # Reward/risk
    rr_str = ""
    entry_price = pos.get("entry_price", 0)
    stop_loss = pos.get("stop_loss", 0)
    target_price = pos.get("target_price", 0)
    if stop_loss and target_price and entry_price:
        risk = abs(entry_price - stop_loss)
        reward = abs(target_price - entry_price)
        if risk > 0:
            rr_str = f"\nReward/risk: {reward / risk:.1f}:1"

    score_str = ""
    strategy_code = pos.get("strategy_code", "")
    if sig:
        if sig.get("rank_score"):
            score_str = f" | Score: {sig['rank_score']:.1f}"
        if sig.get("strategy_code"):
            strategy_code = sig["strategy_code"]

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    body = (
        f"Stock: {stock_info}\n"
        f"Side: Buy\n"
        f"Entry: {entry_price:.2f} | Quantity: {pos['quantity']} shares\n"
        f"Stop: {stop_loss:.2f} | Target: {target_price:.2f}\n"
        f"Strategy: {_strategy_label(strategy_code)}{score_str}"
        f"{rr_str}"
    )
    return title, body


def _format_exit_message(pos: dict, trade: dict) -> tuple[str, str]:
    """Format an exit notification; returns (title, body). pos/trade are serialised dicts."""
    name = pos.get("stock_name") or pos["stock_symbol"]
    pnl = trade["pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"[SIMULATION] Simulated exit: {name} {pnl_sign}{pnl:.2f}"

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    reason = EXIT_REASON_LABELS.get(trade["exit_reason"], trade["exit_reason"])
    body = (
        f"Stock: {stock_info}\n"
        f"Exit reason: {reason}\n"
        f"Entry: {trade['entry_price']:.2f} → Exit: {trade['exit_price']:.2f}\n"
        f"P&L: {pnl_sign}{pnl:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%) | Held: {trade['holding_days']} days"
    )
    return title, body


def _dedup_signals(signals: list[StrategySignalRun]) -> list[tuple[StrategySignalRun, int]]:
    """Deduplicate by (stock_symbol, stock_market), keeping the highest rank_score signal.
    Returns [(signal, strategy_count), ...] sorted by rank_score desc.
    """
    seen: dict[tuple[str, str], tuple[StrategySignalRun, int]] = {}
    for sig in signals:
        key = (sig.stock_symbol, sig.stock_market)
        if key not in seen:
            seen[key] = (sig, 1)
        else:
            _, count = seen[key]
            seen[key] = (seen[key][0], count + 1)
    # The query is already sorted by rank_score desc; keep first-seen order
    return list(seen.values())


def _format_premarket_plan(signals: list[StrategySignalRun], account: PaperTradingAccount) -> tuple[str, str]:
    """Format the pre-market plan; returns (title, body). Signals are deduplicated."""
    title = "[SIMULATION] Pre-market plan"
    if not signals:
        return title, "No candidates today"

    deduped = _dedup_signals(signals)

    lines = [f"Available cash: {account.current_capital:,.2f}\n"]
    lines.append("Today's candidates:")
    for i, (sig, strat_count) in enumerate(deduped, 1):
        name = sig.stock_name or sig.stock_symbol
        from src.modules.administration.stock_link import stock_link_markdown
        link = stock_link_markdown(sig.stock_symbol, sig.stock_market)
        entry_range = ""
        if sig.entry_low and sig.entry_high:
            entry_range = f" Entry range: {sig.entry_low:.2f}-{sig.entry_high:.2f}"
        score_str = f" Score: {sig.rank_score:.1f}" if sig.rank_score else ""
        strat_label = _strategy_label(sig.strategy_code)
        if strat_count > 1:
            strat_label = f"{strat_label} and {strat_count - 1} more"
        lines.append(f"{i}. {name} ({link}){entry_range}{score_str} [{strat_label}]")

    return title, "\n".join(lines)


def _format_daily_summary(
    trades: list[PaperTradingTrade],
    positions: list[PaperTradingPosition],
    account: PaperTradingAccount,
) -> tuple[str, str]:
    """Format the end-of-day summary; returns (title, body)."""
    # Total assets
    positions_value = sum((p.current_price or p.entry_price) * p.quantity for p in positions)
    total_equity = account.current_capital + positions_value
    unrealized = sum(p.unrealized_pnl or 0 for p in positions)

    title = "[SIMULATION] Daily summary"
    lines = [f"Total assets: {total_equity:,.2f}"]

    # Closed today
    if trades:
        day_pnl = sum(t.pnl for t in trades)
        pnl_sign = "+" if day_pnl >= 0 else ""
        lines.append(
            f"\nClosed today: {len(trades)} trade{'' if len(trades) == 1 else 's'}, "
            f"P&L: {pnl_sign}{day_pnl:,.2f}"
        )
        for t in trades:
            s = "+" if t.pnl >= 0 else ""
            reason = EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason)
            lines.append(f"  · {t.stock_name or t.stock_symbol}: {s}{t.pnl:,.2f} ({s}{t.pnl_pct:.2f}%) [{reason}]")
    else:
        lines.append("\nNo trades closed today")

    # Unrealised P&L
    if positions:
        u_sign = "+" if unrealized >= 0 else ""
        lines.append(f"\nOpen positions: {len(positions)}, unrealised P&L: {u_sign}{unrealized:,.2f}")
        for p in positions:
            pnl = p.unrealized_pnl or 0
            s = "+" if pnl >= 0 else ""
            lines.append(f"  · {p.stock_name or p.stock_symbol}: {s}{pnl:,.2f}")
    else:
        lines.append("\nNo open positions")

    lines.append(f"\nAvailable cash: {account.current_capital:,.2f}")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

async def notify_entry(pos: dict, sig: dict | None) -> None:
    """Entry notification (async; failures are only logged). pos/sig are serialised dicts."""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_entry_message(pos, sig)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[Simulation notify] Entry notification failed")


async def notify_exit(pos: dict, trade: dict) -> None:
    """Exit notification (async; failures are only logged). pos/trade are serialised dicts."""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_exit_message(pos, trade)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[Simulation notify] Exit notification failed")


async def send_premarket_plan() -> None:
    """Pre-market plan notification."""
    try:
        if not _is_mode_enabled("pt_notify_premarket"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            # Exclude markets with an investment ratio of 0
            from src.modules.paper_trading.paper_trading_engine import ALL_MARKETS, market_allocations_or_default
            alloc = market_allocations_or_default(account)
            excluded = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
            query = (
                db.query(StrategySignalRun)
                .filter(
                    StrategySignalRun.status == "active",
                    StrategySignalRun.action.in_(["buy", "add"]),
                    StrategySignalRun.entry_low.isnot(None),
                    StrategySignalRun.entry_high.isnot(None),
                )
            )
            if excluded:
                query = query.filter(StrategySignalRun.stock_market.notin_(excluded))
            signals = query.order_by(StrategySignalRun.rank_score.desc()).all()

            title, body = _format_premarket_plan(signals, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[Simulation notify] Pre-market plan failed")


async def send_daily_summary() -> None:
    """End-of-day summary notification."""
    try:
        if not _is_mode_enabled("pt_notify_summary"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Closed today
            trades = (
                db.query(PaperTradingTrade)
                .filter(PaperTradingTrade.closed_at >= today_start)
                .order_by(PaperTradingTrade.closed_at.desc())
                .all()
            )

            # Open positions
            positions = (
                db.query(PaperTradingPosition)
                .filter(PaperTradingPosition.status == "open")
                .all()
            )

            title, body = _format_daily_summary(trades, positions, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[Simulation notify] End-of-day summary failed")


async def send_test_notification() -> dict:
    """Send a test notification and return the result."""
    mgr = _build_notifier()
    if not mgr:
        return {"success": False, "error": "Notifications are off or no channel is available"}
    result = await mgr.notify_with_result(
        "[SIMULATION] Test notification",
        "This is a test notification confirming the channel is set up correctly.",
    )
    return result
