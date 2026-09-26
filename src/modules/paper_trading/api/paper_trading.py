"""Simulation API endpoints."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from src.platform.compliance import Feature
from src.platform.compliance.http import feature_gate
from src.platform.runtime.config import Settings
from src.modules.paper_trading.paper_trading_engine import (
    ALL_MARKETS,
    ENGINE,
    compute_market_cash,
    market_allocations_or_default,
    normalize_allocations,
)
from src.modules.portfolio.portfolio_diagnostics import diagnose_paper_portfolio
from src.modules.strategy.quant_adapters import available_backends
from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    AppSettings,
    NotifyChannel,
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _format_dt(dt) -> str:
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


class ToggleBody(BaseModel):
    enabled: bool


class UpdateSettingsBody(BaseModel):
    excluded_markets: list[str] | None = None  # old field, kept for compatibility
    market_allocations: dict[str, float] | None = None  # {"IN": 1.0}; total <= 1
    initial_capital: float | None = None  # total capital (>0: add/withdraw the difference)


def _serialize_account_dict(
    acc: PaperTradingAccount,
    *,
    initial: float,
    cash: float,
    total_equity: float,
    total_pnl: float,
    unrealized: float,
    total_trades: int,
    winning_trades: int,
    max_dd: float,
    peak: float,
    market: str | None = None,
    ratio: float | None = None,
) -> dict:
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    out = {
        "id": acc.id,
        "initial_capital": round(initial, 2),
        "current_capital": round(cash, 2),
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "win_rate": round(win_rate, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "peak_capital": round(peak, 2),
        "enabled": acc.enabled,
        "excluded_markets": acc.excluded_markets or [],
        "market_allocations": market_allocations_or_default(acc),
        "created_at": _format_dt(acc.created_at),
        "updated_at": _format_dt(acc.updated_at),
    }
    if market:
        out["market"] = market
        out["allocation_ratio"] = round(ratio or 0.0, 6)
    return out


def _build_equity_curve(
    db: Session, acc: PaperTradingAccount, market: str | None
) -> tuple[list[dict], float, float]:
    """Build the return curve; returns (curve, peak, max_drawdown_pct). market=None means all markets."""
    ratio = market_allocations_or_default(acc).get(market, 0.0) if market else 1.0
    base = acc.initial_capital * ratio if market else acc.initial_capital

    tq = db.query(PaperTradingTrade).order_by(PaperTradingTrade.closed_at.asc())
    if market:
        tq = tq.filter(PaperTradingTrade.stock_market == market)
    trades = tq.all()

    pq = db.query(PaperTradingPosition).filter(PaperTradingPosition.status == "open")
    if market:
        pq = pq.filter(PaperTradingPosition.stock_market == market)
    open_positions = pq.all()

    by_date: dict[str, float] = {}
    for t in trades:
        if not t.closed_at:
            continue
        dt = t.closed_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        by_date.setdefault(date_str, 0.0)
        by_date[date_str] += t.pnl

    curve: list[dict] = []
    running = base
    for date_str in sorted(by_date.keys()):
        running += by_date[date_str]
        curve.append({"date": date_str, "equity": round(running, 2)})

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    positions_value = sum(
        (p.current_price or p.entry_price) * p.quantity for p in open_positions
    )
    if market:
        realized = sum(t.pnl for t in trades)
        open_cost = sum(p.entry_price * p.quantity for p in open_positions)
        cash_now = compute_market_cash(acc.initial_capital, ratio, realized, open_cost)
    else:
        cash_now = acc.current_capital
    total_equity_now = cash_now + positions_value
    if curve and curve[-1]["date"] == today_str:
        curve[-1]["equity"] = round(total_equity_now, 2)
    else:
        curve.append({"date": today_str, "equity": round(total_equity_now, 2)})

    if len(curve) == 1:
        created = acc.created_at
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            start_str = created.strftime("%Y-%m-%d")
            if start_str != today_str:
                curve.insert(0, {"date": start_str, "equity": round(base, 2)})
            else:
                curve.insert(0, {"date": today_str + " 00:00", "equity": round(base, 2)})

    peak = base
    max_dd = 0.0
    for pt in curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return curve, peak, max_dd


def _account_summary(db: Session, acc: PaperTradingAccount, market: str | None) -> dict:
    """Account summary. market=None is the whole account (using the engine's drawdown); otherwise that market's sub-pool."""
    if not market or market not in ALL_MARKETS:
        open_positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        unrealized = sum(p.unrealized_pnl or 0 for p in open_positions)
        positions_value = sum(
            (p.current_price or p.entry_price) * p.quantity for p in open_positions
        )
        return _serialize_account_dict(
            acc,
            initial=acc.initial_capital,
            cash=acc.current_capital,
            total_equity=acc.current_capital + positions_value,
            total_pnl=acc.total_pnl,
            unrealized=unrealized,
            total_trades=acc.total_trades,
            winning_trades=acc.winning_trades,
            max_dd=acc.max_drawdown_pct,
            peak=acc.peak_capital,
        )

    alloc = market_allocations_or_default(acc)
    ratio = alloc.get(market, 0.0)
    open_positions = (
        db.query(PaperTradingPosition)
        .filter(
            PaperTradingPosition.status == "open",
            PaperTradingPosition.stock_market == market,
        )
        .all()
    )
    trades = (
        db.query(PaperTradingTrade)
        .filter(PaperTradingTrade.stock_market == market)
        .all()
    )
    realized = sum(t.pnl for t in trades)
    open_cost = sum(p.entry_price * p.quantity for p in open_positions)
    cash = compute_market_cash(acc.initial_capital, ratio, realized, open_cost)
    positions_value = sum(
        (p.current_price or p.entry_price) * p.quantity for p in open_positions
    )
    winning = sum(1 for t in trades if t.pnl > 0)
    _, peak, max_dd = _build_equity_curve(db, acc, market)
    unrealized = sum(p.unrealized_pnl or 0 for p in open_positions)
    return _serialize_account_dict(
        acc,
        initial=acc.initial_capital * ratio,
        cash=cash,
        total_equity=cash + positions_value,
        total_pnl=realized,
        unrealized=unrealized,
        total_trades=len(trades),
        winning_trades=winning,
        max_dd=max_dd,
        peak=peak,
        market=market,
        ratio=ratio,
    )


def _strategy_performance(db: Session, market: str | None) -> list[dict]:
    """Performance by strategy (closed + open), optionally filtered by market."""
    tq = db.query(PaperTradingTrade)
    if market:
        tq = tq.filter(PaperTradingTrade.stock_market == market)
    all_trades = tq.all()

    pq = db.query(PaperTradingPosition).filter(PaperTradingPosition.status == "open")
    if market:
        pq = pq.filter(PaperTradingPosition.stock_market == market)
    open_positions = pq.all()

    strategy_stats: dict[str, dict] = {}
    for t in all_trades:
        code = t.strategy_code or "unknown"
        s = strategy_stats.setdefault(code, {
            "strategy_code": code,
            "total_trades": 0, "winning_trades": 0,
            "total_pnl": 0.0, "total_pnl_pct_sum": 0.0,
            "holding_days_sum": 0,
            "open_positions": 0, "unrealized_pnl": 0.0,
        })
        s["total_trades"] += 1
        s["total_pnl"] += t.pnl
        s["total_pnl_pct_sum"] += t.pnl_pct
        s["holding_days_sum"] += t.holding_days or 0
        if t.pnl > 0:
            s["winning_trades"] += 1

    for p in open_positions:
        code = p.strategy_code or "unknown"
        s = strategy_stats.setdefault(code, {
            "strategy_code": code,
            "total_trades": 0, "winning_trades": 0,
            "total_pnl": 0.0, "total_pnl_pct_sum": 0.0,
            "holding_days_sum": 0,
            "open_positions": 0, "unrealized_pnl": 0.0,
        })
        s["open_positions"] += 1
        s["unrealized_pnl"] += p.unrealized_pnl or 0

    strategy_perf = []
    for s in strategy_stats.values():
        n = s["total_trades"]
        strategy_perf.append({
            "strategy_code": s["strategy_code"],
            "total_trades": n,
            "winning_trades": s["winning_trades"],
            "win_rate": round(s["winning_trades"] / n * 100, 1) if n > 0 else 0,
            "total_pnl": round(s["total_pnl"], 2),
            "avg_pnl_pct": round(s["total_pnl_pct_sum"] / n, 2) if n > 0 else 0,
            "avg_holding_days": round(s["holding_days_sum"] / n, 1) if n > 0 else 0,
            "open_positions": s["open_positions"],
            "unrealized_pnl": round(s["unrealized_pnl"], 2),
        })
    strategy_perf.sort(key=lambda x: x["total_pnl"] + x["unrealized_pnl"], reverse=True)
    return strategy_perf


def _position_response(p: PaperTradingPosition) -> dict:
    holding_days = 0
    if p.opened_at:
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc)
        opened = p.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=tz.utc)
        holding_days = max(0, (now - opened).days)
    return {
        "id": p.id,
        "stock_symbol": p.stock_symbol,
        "stock_market": p.stock_market,
        "stock_name": p.stock_name or "",
        "quantity": p.quantity,
        "entry_price": p.entry_price,
        "stop_loss": p.stop_loss,
        "target_price": p.target_price,
        "current_price": p.current_price,
        "unrealized_pnl": round(p.unrealized_pnl or 0, 2),
        "unrealized_pnl_pct": round(
            ((p.current_price - p.entry_price) / p.entry_price * 100)
            if p.current_price and p.entry_price > 0 else 0, 2
        ),
        "status": p.status,
        "signal_run_id": p.signal_run_id,
        "signal_snapshot_date": p.signal_snapshot_date or "",
        "signal_action": p.signal_action or "",
        "strategy_code": p.strategy_code or "",
        "holding_days": holding_days,
        "opened_at": _format_dt(p.opened_at),
        "closed_at": _format_dt(p.closed_at),
        "updated_at": _format_dt(p.updated_at),
    }


def _trade_response(t: PaperTradingTrade) -> dict:
    return {
        "id": t.id,
        "stock_symbol": t.stock_symbol,
        "stock_market": t.stock_market,
        "stock_name": t.stock_name or "",
        "quantity": t.quantity,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "pnl": round(t.pnl, 2),
        "pnl_pct": round(t.pnl_pct, 2),
        "exit_reason": t.exit_reason,
        "signal_run_id": t.signal_run_id,
        "signal_snapshot_date": t.signal_snapshot_date or "",
        "strategy_code": t.strategy_code or "",
        "holding_days": t.holding_days or 0,
        "opened_at": _format_dt(t.opened_at),
        "closed_at": _format_dt(t.closed_at),
    }


@router.get("/account")
def get_account(market: str | None = None, db: Session = Depends(get_db)):
    acc = db.query(PaperTradingAccount).first()
    if not acc:
        acc = PaperTradingAccount(
            initial_capital=1000000.0,
            current_capital=1000000.0,
            peak_capital=1000000.0,
        )
        db.add(acc)
        db.commit()
        db.refresh(acc)
    return _account_summary(db, acc, market if market in ALL_MARKETS else None)


@router.get("/positions")
def list_positions(status: str = "open", market: str | None = None, db: Session = Depends(get_db)):
    query = db.query(PaperTradingPosition)
    if status != "all":
        query = query.filter(PaperTradingPosition.status == status)
    if market in ALL_MARKETS:
        query = query.filter(PaperTradingPosition.stock_market == market)
    rows = query.order_by(PaperTradingPosition.opened_at.desc()).all()
    return [_position_response(p) for p in rows]


@router.get("/trades")
def list_trades(limit: int = 50, offset: int = 0, market: str | None = None, db: Session = Depends(get_db)):
    base = db.query(PaperTradingTrade)
    if market in ALL_MARKETS:
        base = base.filter(PaperTradingTrade.stock_market == market)
    total = base.count()
    rows = (
        base.order_by(PaperTradingTrade.closed_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return {
        "total": total,
        "items": [_trade_response(t) for t in rows],
    }


@router.get("/metrics")
def get_metrics(market: str | None = None, db: Session = Depends(get_db)):
    acc = db.query(PaperTradingAccount).first()
    if not acc:
        return {"account": None, "equity_curve": [], "open_positions": 0, "strategy_performance": []}

    mkt = market if market in ALL_MARKETS else None

    pq = db.query(PaperTradingPosition).filter(PaperTradingPosition.status == "open")
    if mkt:
        pq = pq.filter(PaperTradingPosition.stock_market == mkt)
    open_count = pq.count()

    equity_curve, _peak, _max_dd = _build_equity_curve(db, acc, mkt)

    return {
        "account": _account_summary(db, acc, mkt),
        "equity_curve": equity_curve,
        "open_positions": open_count,
        "strategy_performance": _strategy_performance(db, mkt),
    }


@router.get("/diagnostics")
def get_diagnostics():
    """Portfolio diagnostics (read-only): concentration / market and strategy split / risk notes."""
    return diagnose_paper_portfolio()


@router.get("/backends")
def get_backends():
    """Detect available back-test backends (builtin always; vectorbt/rqalpha/qlib if installed)."""
    return available_backends()


@router.post(
    "/account/toggle",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
def toggle_account(body: ToggleBody, db: Session = Depends(get_db)):
    acc = db.query(PaperTradingAccount).first()
    if not acc:
        raise HTTPException(404, "Simulation account not found")
    acc.enabled = body.enabled
    db.commit()
    db.refresh(acc)
    return _account_summary(db, acc, None)


@router.post("/account/reset")
def reset_account():
    result = ENGINE.reset_account()
    if not result.get("ok"):
        raise HTTPException(500, "Reset failed")
    return {"ok": True}


@router.post("/positions/{position_id}/close")
async def close_position(position_id: int):
    result = await ENGINE.close_position_manual_async(position_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Close failed"))
    return {"ok": True}


@router.post("/account/settings")
def update_settings(body: UpdateSettingsBody, db: Session = Depends(get_db)):
    acc = db.query(PaperTradingAccount).first()
    if not acc:
        raise HTTPException(404, "Simulation account not found")

    if body.market_allocations is not None:
        alloc = normalize_allocations(body.market_allocations)
        total = sum(alloc.values())
        if total > 1.0 + 1e-9:
            raise HTTPException(400, f"Investment ratios can't add up to more than 100% (currently {round(total * 100)}%)")
        acc.market_allocations = alloc
        # Derive excluded_markets too (ratio 0 = excluded), for old readers
        acc.excluded_markets = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
    elif body.excluded_markets is not None:
        valid = {"IN"}
        acc.excluded_markets = [m for m in body.excluded_markets if m in valid]

    if body.initial_capital is not None and body.initial_capital > 0:
        delta = body.initial_capital - (acc.initial_capital or 0.0)
        acc.initial_capital = body.initial_capital
        acc.current_capital = (acc.current_capital or 0.0) + delta
        if acc.peak_capital is None or acc.current_capital > acc.peak_capital:
            acc.peak_capital = acc.current_capital

    db.commit()
    db.refresh(acc)
    return _account_summary(db, acc, None)


@router.post(
    "/scan",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
async def manual_scan():
    """Run one simulation scan manually (entries + exit checks)."""
    result = await ENGINE.scan_once()
    return result


# ---------------------------------------------------------------------------
# Simulation notification settings
# ---------------------------------------------------------------------------

_NOTIFY_KEYS = [
    "pt_notify_enabled",
    "pt_notify_channel_ids",
    "pt_notify_realtime",
    "pt_notify_premarket",
    "pt_notify_summary",
]

_NOTIFY_DEFAULTS = {
    "pt_notify_enabled": "false",
    "pt_notify_channel_ids": "",
    "pt_notify_realtime": "true",
    "pt_notify_premarket": "true",
    "pt_notify_summary": "true",
}


@router.get("/notify-settings")
def get_notify_settings(db: Session = Depends(get_db)):
    """Current notification config + available channels."""
    rows = db.query(AppSettings).filter(AppSettings.key.in_(_NOTIFY_KEYS)).all()
    settings = dict(_NOTIFY_DEFAULTS)
    for r in rows:
        settings[r.key] = r.value or _NOTIFY_DEFAULTS.get(r.key, "")

    channels = db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all()
    channel_list = [
        {"id": ch.id, "name": ch.name, "type": ch.type, "is_default": ch.is_default}
        for ch in channels
    ]

    return {"settings": settings, "channels": channel_list}


class NotifySettingsBody(BaseModel):
    pt_notify_enabled: str | None = None
    pt_notify_channel_ids: str | None = None
    pt_notify_realtime: str | None = None
    pt_notify_premarket: str | None = None
    pt_notify_summary: str | None = None


@router.post(
    "/notify-settings",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
def update_notify_settings(body: NotifySettingsBody, db: Session = Depends(get_db)):
    """Update the notification config."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    for key, value in updates.items():
        row = db.query(AppSettings).filter(AppSettings.key == key).first()
        if row:
            row.value = value
        else:
            db.add(AppSettings(key=key, value=value, description=f"Simulation notification config: {key}"))
    db.commit()
    return get_notify_settings(db)


@router.post(
    "/notify-test",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
async def test_notify():
    """Send a test notification."""
    from src.modules.paper_trading.paper_trading_notifier import send_test_notification
    result = await send_test_notification()
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Send failed"))
    return {"ok": True}


@router.post(
    "/premarket-plan",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
async def trigger_premarket_plan():
    """Send the pre-market plan notification manually."""
    from src.modules.paper_trading.paper_trading_notifier import send_premarket_plan
    await send_premarket_plan()
    return {"ok": True}


@router.post(
    "/daily-summary",
    dependencies=[Depends(feature_gate(Feature.AI_PAPER_TRADING))],
)
async def trigger_daily_summary():
    """Send the end-of-day summary notification manually."""
    from src.modules.paper_trading.paper_trading_notifier import send_daily_summary
    await send_daily_summary()
    return {"ok": True}
