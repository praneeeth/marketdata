"""Simulation engine: opens and closes positions from strategy signals and tracks a virtual account's returns."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.models import MarketCode, MARKETS
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
    StrategySignalRun,
)
from src.modules.strategy.backtest.cost_model import CostModel

logger = logging.getLogger(__name__)

# Simulation trading costs (inherited China A-share parameters, Phase 1; not yet calibrated to Indian STT/brokerage). Shared with the back-test cost model.
COST_MODEL = CostModel()

# Minimum order quantity (inherited A-share board lot of 100; NSE cash equities trade in single shares)
FIXED_QUANTITY = 100

# Trailing exit: active once unrealised profit exceeds MIN_PROFIT_FOR_TRAILING; exits when price falls TRAILING_STOP_PCT from the high while held
MIN_PROFIT_FOR_TRAILING = 0.05
TRAILING_STOP_PCT = 0.10
# Time exit: default maximum holding period in calendar days when the signal has no holding_days
DEFAULT_TIME_STOP_DAYS = 20


def _position_weight(rank_score: float) -> float:
    """Share of the market budget to put into one trade, by signal strength (higher rank_score, more money)."""
    s = float(rank_score or 0.0)
    if s >= 85:
        return 0.25
    if s >= 75:
        return 0.18
    if s >= 65:
        return 0.12
    return 0.08


def _compute_quantity(
    *,
    rank_score: float,
    market_budget: float,
    price: float,
    available_cash: float,
    cost_model: CostModel,
    lot: int = FIXED_QUANTITY,
) -> int:
    """Number of shares to buy (a multiple of lot) from signal strength and market budget, limited by available cash (including the buy fee).

    Returns 0 when even one lot is unaffordable; the caller should skip.
    """
    if price <= 0:
        return 0
    target_cash = max(0.0, market_budget) * _position_weight(rank_score)
    qty = int((target_cash / price) // lot) * lot
    if qty < lot:
        qty = lot  # at least one lot
    while qty >= lot:
        outlay = -cost_model.fill("buy", price, qty).cash_delta
        if outlay <= available_cash:
            return qty
        qty -= lot
    return 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except Exception:
        return MarketCode.IN


def _is_trading_time(market: str) -> bool:
    mc = _to_market(market)
    market_def = MARKETS.get(mc)
    if not market_def:
        return False
    return market_def.is_trading_time()


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cash allocation per market (investment ratio -> sub-pool cash)
# ---------------------------------------------------------------------------

ALL_MARKETS: tuple[str, ...] = ("IN",)
DEFAULT_ALLOCATIONS: dict[str, float] = {"IN": 1.0}


def normalize_allocations(raw: dict | None) -> dict[str, float]:
    """Fill in every market, clamp to [0, 1]; returns {market: ratio}."""
    raw = raw or {}
    out: dict[str, float] = {}
    for m in ALL_MARKETS:
        try:
            v = float(raw.get(m, 0.0) or 0.0)
        except Exception:
            v = 0.0
        out[m] = min(1.0, max(0.0, v))
    return out


def market_allocations_or_default(account: Any) -> dict[str, float]:
    """Fall back to the default allocation when the account has none; otherwise normalise the configured ratios."""
    raw = getattr(account, "market_allocations", None) or {}
    if not raw:
        return dict(DEFAULT_ALLOCATIONS)
    return normalize_allocations(raw)


def allocations_from_excluded(excluded: list[str] | None) -> dict[str, float]:
    """For migration: excluded markets get ratio 0; the rest are normalised to sum to 1.0 by default weight."""
    excluded_set = {str(m).upper() for m in (excluded or [])}
    weights = {m: DEFAULT_ALLOCATIONS[m] for m in ALL_MARKETS if m not in excluded_set}
    total = sum(weights.values())
    if total <= 0:
        # Everything excluded: fall back to India, the only market.
        return {"IN": 1.0}
    return {m: round(weights.get(m, 0.0) / total, 6) for m in ALL_MARKETS}


def compute_market_cash(
    initial_capital: float, ratio: float, realized_pnl: float, open_cost: float
) -> float:
    """Available cash for a market = total capital x ratio + that market's realised P&L - that market's position cost (pure function, unit-testable)."""
    return initial_capital * ratio + realized_pnl - open_cost


def market_realized_open(db: Session, market: str) -> tuple[float, float]:
    """Return (the market's total realised P&L, the market's total open position cost)."""
    realized = (
        db.query(func.coalesce(func.sum(PaperTradingTrade.pnl), 0.0))
        .filter(PaperTradingTrade.stock_market == market)
        .scalar()
    ) or 0.0
    open_cost = (
        db.query(
            func.coalesce(
                func.sum(PaperTradingPosition.entry_price * PaperTradingPosition.quantity),
                0.0,
            )
        )
        .filter(
            PaperTradingPosition.status == "open",
            PaperTradingPosition.stock_market == market,
        )
        .scalar()
    ) or 0.0
    return float(realized), float(open_cost)


def market_available_cash(
    db: Session, account: PaperTradingAccount, market: str, alloc: dict | None = None
) -> float:
    """A market's current available cash (for the entry threshold and display)."""
    alloc = alloc or market_allocations_or_default(account)
    ratio = alloc.get(market, 0.0)
    realized, open_cost = market_realized_open(db, market)
    return compute_market_cash(account.initial_capital, ratio, realized, open_cost)


def _serialize_position(pos: PaperTradingPosition) -> dict:
    """Copy an ORM Position into a plain dict to avoid detached-instance problems."""
    return {
        "id": pos.id,
        "stock_symbol": pos.stock_symbol,
        "stock_market": pos.stock_market,
        "stock_name": pos.stock_name or "",
        "quantity": pos.quantity,
        "entry_price": pos.entry_price,
        "stop_loss": pos.stop_loss,
        "target_price": pos.target_price,
        "current_price": pos.current_price,
        "unrealized_pnl": pos.unrealized_pnl,
        "status": pos.status,
        "strategy_code": pos.strategy_code or "",
    }


def _serialize_trade(trade: PaperTradingTrade) -> dict:
    """Copy an ORM Trade into a plain dict."""
    return {
        "id": trade.id,
        "stock_symbol": trade.stock_symbol,
        "stock_market": trade.stock_market,
        "stock_name": trade.stock_name or "",
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "pnl": trade.pnl,
        "pnl_pct": trade.pnl_pct,
        "exit_reason": trade.exit_reason,
        "holding_days": trade.holding_days,
        "strategy_code": trade.strategy_code or "",
    }


def _serialize_signal(sig: StrategySignalRun) -> dict:
    """Copy an ORM Signal into a plain dict."""
    return {
        "id": sig.id,
        "stock_symbol": sig.stock_symbol,
        "stock_market": sig.stock_market,
        "stock_name": sig.stock_name or "",
        "strategy_code": sig.strategy_code or "",
        "rank_score": sig.rank_score,
        "entry_low": sig.entry_low,
        "entry_high": sig.entry_high,
        "action": sig.action,
    }


class PaperTradingEngine:
    """Simulation scan engine."""

    def _get_or_create_account(self, db: Session) -> PaperTradingAccount:
        account = db.query(PaperTradingAccount).first()
        if not account:
            account = PaperTradingAccount(
                initial_capital=1000000.0,
                current_capital=1000000.0,
                peak_capital=1000000.0,
            )
            db.add(account)
            db.commit()
            db.refresh(account)
        return account

    def _fetch_quotes_map(self, symbols_markets: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Fetch quotes in bulk; returns {(market, symbol): quote_dict}.

        Goes through QuoteOrchestrator, which supports failover across providers.
        """
        grouped: dict[MarketCode, list[str]] = {}
        for symbol, market in symbols_markets:
            mc = _to_market(market)
            grouped.setdefault(mc, []).append(symbol)

        out: dict[tuple[str, str], dict] = {}
        for market, symbols in grouped.items():
            if not symbols:
                continue
            rows = md_quote_rows(symbols, market.value)
            by_symbol = {str(r.get("symbol")): r for r in rows}
            for sym in symbols:
                q = by_symbol.get(sym)
                if q:
                    out[(market.value, sym)] = q
        return out

    def _check_entries(
        self, db: Session, account: PaperTradingAccount,
    ) -> tuple[int, set[tuple[str, str]], list[tuple[PaperTradingPosition, StrategySignalRun | None]]]:
        """Check strategy signals that allow entry and open positions. Returns (opened count, keys of newly opened stocks, entry events)."""
        # Latest active buy signals
        query = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.status == "active",
                StrategySignalRun.action.in_(["buy", "add"]),
                StrategySignalRun.entry_low.isnot(None),
                StrategySignalRun.entry_high.isnot(None),
            )
        )
        # Exclude markets with an investment ratio of 0
        alloc = market_allocations_or_default(account)
        excluded = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
        if excluded:
            query = query.filter(StrategySignalRun.stock_market.notin_(excluded))
        signals = query.order_by(StrategySignalRun.rank_score.desc()).limit(50).all()
        entry_events: list[tuple[PaperTradingPosition, StrategySignalRun | None]] = []
        new_keys: set[tuple[str, str]] = set()
        if not signals:
            return 0, new_keys, entry_events

        # Stocks that already have an open position
        open_keys = set()
        open_positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        for p in open_positions:
            open_keys.add((p.stock_symbol, p.stock_market))

        # Signals that need quotes (deduped: keep the highest rank_score per stock)
        candidates = []
        seen = set()
        for sig in signals:
            key = (sig.stock_symbol, sig.stock_market)
            if key in open_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            candidates.append(sig)

        if not candidates:
            return 0, new_keys, entry_events

        # Fetch quotes in bulk
        syms = [(s.stock_symbol, s.stock_market) for s in candidates]
        quotes = self._fetch_quotes_map(syms)

        # Available cash per market (deducted trade by trade from the market's sub-pool)
        market_cash = {m: market_available_cash(db, account, m, alloc) for m in ALL_MARKETS}

        opened = 0
        for sig in candidates:
            key = (sig.stock_market, sig.stock_symbol)
            quote = quotes.get(key)
            if not quote:
                continue
            current_price = _safe_float(quote.get("current_price"))
            if current_price is None or current_price <= 0:
                continue

            # Enter at the current market price
            entry_price = current_price
            mkt = sig.stock_market
            if alloc.get(mkt, 0.0) <= 0:
                continue  # market ratio is 0; don't invest
            avail = market_cash.get(mkt, 0.0)

            # Position sizing: allocate the market budget by signal strength (replaces the old fixed 100 shares)
            market_budget = account.initial_capital * alloc.get(mkt, 0.0)
            quantity = _compute_quantity(
                rank_score=float(sig.rank_score or 0.0),
                market_budget=market_budget,
                price=entry_price,
                available_cash=avail,
                cost_model=COST_MODEL,
            )
            if quantity <= 0:
                continue  # not enough in the sub-pool for the minimum lot

            # Actual buy outflow including trading costs
            buy_fill = COST_MODEL.fill("buy", entry_price, quantity)
            buy_outlay = -buy_fill.cash_delta

            # Stop loss / take profit from the entry price
            # Prefer the signal's stop/target ratios; otherwise the defaults of -8% / +15%
            stop_loss = sig.stop_loss
            target_price = sig.target_price
            if stop_loss and sig.entry_low and sig.entry_low > 0:
                # Keep the signal's stop ratio, mapped onto the actual entry price
                orig_mid = (sig.entry_low + (sig.entry_high or sig.entry_low)) / 2
                if orig_mid > 0:
                    stop_ratio = (stop_loss - orig_mid) / orig_mid
                    target_ratio = ((target_price - orig_mid) / orig_mid) if target_price else 0.15
                    stop_loss = round(entry_price * (1 + stop_ratio), 4)
                    target_price = round(entry_price * (1 + target_ratio), 4) if target_price else None
            # Fallback: default -8% when the stop is unreasonable
            if not stop_loss or stop_loss <= 0 or stop_loss >= entry_price:
                stop_loss = round(entry_price * 0.92, 4)
            # Fallback: default +15% when the target is unreasonable
            if not target_price or target_price <= 0 or target_price <= entry_price:
                target_price = round(entry_price * 1.15, 4)

            pos = PaperTradingPosition(
                stock_symbol=sig.stock_symbol,
                stock_market=sig.stock_market,
                stock_name=sig.stock_name or "",
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                current_price=current_price,
                highest_price=entry_price,
                unrealized_pnl=0.0,
                status="open",
                signal_run_id=sig.id,
                signal_snapshot_date=sig.snapshot_date or "",
                signal_action=sig.action or "",
                strategy_code=sig.strategy_code or "",
            )
            db.add(pos)
            account.current_capital -= buy_outlay
            market_cash[mkt] = avail - buy_outlay
            open_keys.add((sig.stock_symbol, sig.stock_market))
            new_keys.add((sig.stock_symbol, sig.stock_market))
            entry_events.append((pos, sig))
            opened += 1
            logger.info(
                "[Simulation] Opened: %s %s @ %.2f x%d, stop=%.2f, target=%s, buy costs=%.2f, strategy=%s",
                sig.stock_name or sig.stock_symbol,
                sig.stock_market,
                entry_price,
                quantity,
                stop_loss or 0,
                target_price or "none",
                buy_fill.explicit_fees + buy_fill.slippage_cost,
                sig.strategy_code,
            )

        if opened > 0:
            db.commit()
        return opened, new_keys, entry_events

    def _close_position(
        self,
        db: Session,
        account: PaperTradingAccount,
        pos: PaperTradingPosition,
        exit_price: float,
        exit_reason: str,
    ) -> PaperTradingTrade:
        """Close one position and return the trade record."""
        now = _utc_now()
        # Net P&L including trading costs: net sell proceeds - entry outlay including fees (same basis as entry; cash is conserved)
        buy_cost = -COST_MODEL.fill("buy", pos.entry_price, pos.quantity).cash_delta
        sell_fill = COST_MODEL.fill("sell", exit_price, pos.quantity)
        sell_proceeds = sell_fill.cash_delta
        pnl = round(sell_proceeds - buy_cost, 4)
        pnl_pct = (pnl / buy_cost * 100) if buy_cost > 0 else 0.0

        holding_days = 0
        if pos.opened_at:
            opened = pos.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            holding_days = max(0, (now - opened).days)

        trade = PaperTradingTrade(
            stock_symbol=pos.stock_symbol,
            stock_market=pos.stock_market,
            stock_name=pos.stock_name or "",
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=round(pnl_pct, 2),
            exit_reason=exit_reason,
            signal_run_id=pos.signal_run_id,
            signal_snapshot_date=pos.signal_snapshot_date or "",
            strategy_code=pos.strategy_code or "",
            holding_days=holding_days,
            opened_at=pos.opened_at,
            closed_at=now,
        )
        db.add(trade)

        pos.status = "closed"
        pos.closed_at = now
        pos.current_price = exit_price
        pos.unrealized_pnl = pnl

        # Returned cash (net sell proceeds, sell fee already deducted)
        account.current_capital += sell_proceeds
        account.total_pnl += pnl
        account.total_trades += 1
        if pnl > 0:
            account.winning_trades += 1

        logger.info(
            "[Simulation] Closed: %s %s @ %.2f, P&L=%.2f (%.2f%%), reason=%s",
            pos.stock_name or pos.stock_symbol,
            pos.stock_market,
            exit_price,
            pnl,
            pnl_pct,
            exit_reason,
        )
        return trade

    def _check_exits(
        self, db: Session, account: PaperTradingAccount, skip_keys: set[tuple[str, str]] | None = None,
    ) -> tuple[int, list[tuple[PaperTradingPosition, PaperTradingTrade]]]:
        """Check positions for stop loss / take profit / signal reversal and close them. Stocks in skip_keys are skipped (opened this round)."""
        exit_events: list[tuple[PaperTradingPosition, PaperTradingTrade]] = []
        positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        if not positions:
            return 0, exit_events

        # Fetch quotes in bulk
        syms = [(p.stock_symbol, p.stock_market) for p in positions]
        quotes = self._fetch_quotes_map(syms)

        closed = 0
        for pos in positions:
            # Skip positions opened this round
            if skip_keys and (pos.stock_symbol, pos.stock_market) in skip_keys:
                continue
            key = (pos.stock_market, pos.stock_symbol)
            quote = quotes.get(key)
            current_price = _safe_float(quote.get("current_price")) if quote else None

            if current_price is None or current_price <= 0:
                continue

            # Update the current price, net unrealised P&L (including both-way costs if closed now) and the high while held
            pos.current_price = current_price
            _buy_cost_u = -COST_MODEL.fill("buy", pos.entry_price, pos.quantity).cash_delta
            _sell_u = COST_MODEL.fill("sell", current_price, pos.quantity).cash_delta
            pos.unrealized_pnl = round(_sell_u - _buy_cost_u, 4)
            if pos.highest_price is None or current_price > pos.highest_price:
                pos.highest_price = current_price

            # Stop loss
            if pos.stop_loss and current_price <= pos.stop_loss:
                trade = self._close_position(db, account, pos, current_price, "stop_loss")
                exit_events.append((pos, trade))
                closed += 1
                continue

            # Take profit
            if pos.target_price and current_price >= pos.target_price:
                trade = self._close_position(db, account, pos, current_price, "target_price")
                exit_events.append((pos, trade))
                closed += 1
                continue

            # Trailing exit: once profit qualifies, exit when price falls past the threshold from the high while held
            if pos.highest_price and pos.entry_price > 0:
                profit_ratio = (pos.highest_price - pos.entry_price) / pos.entry_price
                if profit_ratio >= MIN_PROFIT_FOR_TRAILING:
                    trail_line = pos.highest_price * (1 - TRAILING_STOP_PCT)
                    if current_price <= trail_line:
                        trade = self._close_position(db, account, pos, current_price, "trailing_stop")
                        exit_events.append((pos, trade))
                        closed += 1
                        continue

            # Signal reversal
            if pos.signal_run_id:
                # no_autoflush: the signal query is read-only; don't flush this round's accumulated price updates early,
                # or the scan keeps grabbing the SQLite write lock and hits "database is locked" alongside other schedulers.
                # All writes land in one db.commit() at the end of this method.
                with db.no_autoflush:
                    latest = (
                        db.query(StrategySignalRun)
                        .filter(
                            StrategySignalRun.stock_symbol == pos.stock_symbol,
                            StrategySignalRun.stock_market == pos.stock_market,
                            StrategySignalRun.status == "active",
                        )
                        .order_by(StrategySignalRun.created_at.desc())
                        .first()
                    )
                if latest and latest.action in ("sell", "reduce"):
                    trade = self._close_position(db, account, pos, current_price, "signal_reversal")
                    exit_events.append((pos, trade))
                    closed += 1
                    continue

            # Time exit: leave after the maximum calendar days (the signal's holding_days first)
            max_days = DEFAULT_TIME_STOP_DAYS
            if pos.signal_run_id:
                sig_hold = (
                    db.query(StrategySignalRun.holding_days)
                    .filter(StrategySignalRun.id == pos.signal_run_id)
                    .scalar()
                )
                if sig_hold and int(sig_hold) > 0:
                    max_days = int(sig_hold)
            if pos.opened_at:
                opened_dt = pos.opened_at
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                if (_utc_now() - opened_dt).days >= max_days:
                    trade = self._close_position(db, account, pos, current_price, "time_stop")
                    exit_events.append((pos, trade))
                    closed += 1
                    continue

        self._update_account_metrics(db, account)
        db.commit()
        return closed, exit_events

    def _update_account_metrics(self, db: Session, account: PaperTradingAccount) -> None:
        """Update the account's peak and maximum drawdown."""
        # Total assets including unrealised P&L
        open_positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        unrealized_total = sum(p.unrealized_pnl or 0 for p in open_positions)
        total_equity = account.current_capital + sum(
            (p.current_price or p.entry_price) * p.quantity for p in open_positions
        )

        if total_equity > account.peak_capital:
            account.peak_capital = total_equity

        if account.peak_capital > 0:
            drawdown = (account.peak_capital - total_equity) / account.peak_capital * 100
            if drawdown > account.max_drawdown_pct:
                account.max_drawdown_pct = round(drawdown, 2)

    def _scan_sync(self) -> dict:
        """Synchronous scan (runs in a thread)."""
        db = SessionLocal()
        try:
            account = self._get_or_create_account(db)
            if not account.enabled:
                return {"status": "disabled"}

            opened, new_keys, entry_events = self._check_entries(db, account)
            closed, exit_events = self._check_exits(db, account, skip_keys=new_keys)

            # Serialise ORM objects to dicts before db.close() to avoid detached-instance problems
            serialized_entries = [
                {"pos_data": _serialize_position(pos), "sig_data": _serialize_signal(sig) if sig else None}
                for pos, sig in entry_events
            ]
            serialized_exits = [
                {"pos_data": _serialize_position(pos), "trade_data": _serialize_trade(trade)}
                for pos, trade in exit_events
            ]

            return {
                "status": "ok",
                "opened": opened,
                "closed": closed,
                "entry_events": serialized_entries,
                "exit_events": serialized_exits,
            }
        except Exception as e:
            logger.exception(f"[Simulation] Scan failed: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            db.close()

    async def scan_once(self) -> dict:
        """Async scan entry point."""
        result = await asyncio.to_thread(self._scan_sync)
        # Send notifications (async; failures don't affect trading)
        await self._send_notifications(result)
        return result

    def close_position_manual(self, position_id: int) -> dict:
        """Close a position manually."""
        db = SessionLocal()
        try:
            account = self._get_or_create_account(db)
            pos = (
                db.query(PaperTradingPosition)
                .filter(
                    PaperTradingPosition.id == position_id,
                    PaperTradingPosition.status == "open",
                )
                .first()
            )
            if not pos:
                return {"ok": False, "error": "Position not found or already closed"}

            # Latest quote (through the flag-gated md_quote_rows, with failover)
            mc = _to_market(pos.stock_market)
            rows = md_quote_rows([pos.stock_symbol], mc.value)

            exit_price = pos.current_price or pos.entry_price
            if rows:
                p = _safe_float(rows[0].get("current_price"))
                if p and p > 0:
                    exit_price = p

            trade = self._close_position(db, account, pos, exit_price, "manual")
            self._update_account_metrics(db, account)
            db.commit()
            # Serialise before returning so ORM objects aren't detached after db.close()
            return {
                "ok": True,
                "pos_data": _serialize_position(pos),
                "trade_data": _serialize_trade(trade),
            }
        finally:
            db.close()

    async def close_position_manual_async(self, position_id: int) -> dict:
        """Async manual close, with notification."""
        result = await asyncio.to_thread(self.close_position_manual, position_id)
        if result.get("ok"):
            try:
                from src.modules.paper_trading.paper_trading_notifier import notify_exit
                pos_data = result.pop("pos_data", None)
                trade_data = result.pop("trade_data", None)
                if pos_data and trade_data:
                    await notify_exit(pos_data, trade_data)
            except Exception:
                logger.exception("[Simulation] Manual close notification failed")
        return result

    async def _send_notifications(self, result: dict) -> None:
        """Take the serialised events from a scan result and send notifications."""
        try:
            from src.modules.paper_trading.paper_trading_notifier import notify_entry, notify_exit

            for evt in result.pop("entry_events", []):
                await notify_entry(evt["pos_data"], evt.get("sig_data"))
            for evt in result.pop("exit_events", []):
                await notify_exit(evt["pos_data"], evt["trade_data"])
        except Exception:
            logger.exception("[Simulation] Notification failed")

    def reset_account(self) -> dict:
        """Reset the simulation (clears all data)."""
        db = SessionLocal()
        try:
            db.query(PaperTradingPosition).delete()
            db.query(PaperTradingTrade).delete()
            account = db.query(PaperTradingAccount).first()
            if account:
                account.current_capital = account.initial_capital
                account.total_pnl = 0.0
                account.total_trades = 0
                account.winning_trades = 0
                account.max_drawdown_pct = 0.0
                account.peak_capital = account.initial_capital
                account.enabled = True
            db.commit()
            return {"ok": True}
        finally:
            db.close()


ENGINE = PaperTradingEngine()
