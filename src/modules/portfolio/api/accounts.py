"""Account and position management API."""
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from datetime import datetime, timedelta, timezone

from src.platform.persistence.database import get_db
from src.platform.persistence.models import Account, PriceAlertRule, Position, Stock
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()







# ========== Pydantic Models ==========

class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    enabled: bool

    class Config:
        from_attributes = True


class PositionCreate(BaseModel):
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str | None = None  # short: short term, swing: swing, long: long term


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    sort_order: int
    trading_style: str | None
    # Related info
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    class Config:
        from_attributes = True


class PositionReorderItem(BaseModel):
    id: int
    sort_order: int


class PositionReorderRequest(BaseModel):
    items: list[PositionReorderItem]


# ========== Account Endpoints ==========

@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(db: Session = Depends(get_db)):
    """List all accounts."""
    return db.query(Account).order_by(Account.id).all()


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """Get one account."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    """Create an account."""
    account = Account(name=data.name, available_funds=data.available_funds)
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"Created account: {account.name}")
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    """Update an account."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    if data.name is not None:
        account.name = data.name
    if data.available_funds is not None:
        account.available_funds = data.available_funds
    if data.enabled is not None:
        account.enabled = data.enabled

    db.commit()
    db.refresh(account)
    logger.info(f"Updated account: {account.name}")
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """Delete an account (also deletes all of its positions)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    # Read relationship-independent values before commit.  SQLAlchemy expires
    # and detaches deleted instances, so accessing ``account.name`` after the
    # commit may trigger a lazy load and raise DetachedInstanceError.
    account_name = account.name
    db.delete(account)
    db.commit()
    logger.info("Deleted account: %s", account_name)
    return {"success": True}


# ========== Position Endpoints ==========

@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    account_id: int | None = None,
    stock_id: int | None = None,
    db: Session = Depends(get_db)
):
    """List positions, optionally filtered by account or stock."""
    query = db.query(Position)
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)

    positions = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()).all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "sort_order": pos.sort_order or 0,
            "trading_style": pos.trading_style,
            "account_name": pos.account.name if pos.account else None,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
        })
    return result


@router.post("/positions", response_model=PositionResponse)
def create_position(data: PositionCreate, db: Session = Depends(get_db)):
    """Create a position."""
    # Check the account and stock exist
    account = db.query(Account).filter(Account.id == data.account_id).first()
    if not account:
        raise HTTPException(400, "Account not found")

    stock = db.query(Stock).filter(Stock.id == data.stock_id).first()
    if not stock:
        raise HTTPException(400, "Stock not found")

    # Does this account already hold this stock?
    existing = db.query(Position).filter(
        Position.account_id == data.account_id,
        Position.stock_id == data.stock_id,
    ).first()
    if existing:
        raise HTTPException(400, f"Account {account.name} already holds {stock.name}; edit the existing position")

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == data.account_id
    ).scalar() or 0

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        sort_order=int(max_order) + 1,
        trading_style=data.trading_style,
    )
    db.add(position)
    db.commit()
    db.refresh(position)

    logger.info(f"Created position: {account.name} - {stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db)):
    """Update a position."""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "Position not found")

    if data.cost_price is not None:
        position.cost_price = data.cost_price
    if data.quantity is not None:
        position.quantity = data.quantity
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
    if data.trading_style is not None:
        # An empty string clears the value (None)
        position.trading_style = data.trading_style if data.trading_style else None

    db.commit()
    db.refresh(position)

    logger.info(f"Updated position: {position.account.name} - {position.stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db)):
    """Delete a position."""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "Position not found")

    # Capture lazy relationships before the row is deleted/committed.  The
    # deleted Position is no longer session-bound afterwards; logging its
    # relationships at that point can raise DetachedInstanceError.
    account_name = position.account.name if position.account else "Unknown account"
    stock_name = position.stock.name if position.stock else "Unknown stock"
    db.delete(position)
    db.commit()
    logger.info("Deleted position: %s - %s", account_name, stock_name)
    return {"success": True}


@router.put("/positions/reorder/batch")
def reorder_positions(data: PositionReorderRequest, db: Session = Depends(get_db)):
    """Update the position sort order in bulk."""
    if not data.items:
        return {"updated": 0}
    ids = [int(x.id) for x in data.items]
    rows = db.query(Position).filter(Position.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in data.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


# ========== Portfolio Summary ==========

@router.get("/portfolio/summary")
def get_portfolio_summary(
    account_id: int | None = None,
    include_quotes: bool = True,
    db: Session = Depends(get_db),
):
    """
    Portfolio summary.

    Args:
        account_id: optional account id; all accounts when omitted

    Returns:
        accounts: accounts with their positions
        total: totals across all accounts
    """
    # Accounts
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.enabled == True).all()
    else:
        accounts = db.query(Account).filter(Account.enabled == True).all()

    if not accounts:
        return {
            "accounts": [],
            "total": {
                "total_market_value": 0,
                "total_cost": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "available_funds": 0,
                "total_assets": 0,
            }
        }

    # All related stocks
    all_stock_ids = set()
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}

    # Live quotes (optional)
    quotes = _fetch_quotes_for_stocks(stocks) if include_quotes else {}

    # Positions per account
    account_summaries = []
    grand_total_market_value = 0
    grand_total_cost = 0
    grand_available_funds = 0
    grand_daily_pnl = 0

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0
        acc_daily_pnl = 0

        positions_sorted = sorted(
            list(acc.positions or []),
            key=lambda p: (int(getattr(p, "sort_order", 0) or 0), int(p.id)),
        )
        for pos in positions_sorted:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            quote = quotes.get(stock.symbol)
            current_price = quote["current_price"] if quote else None
            change_pct = quote["change_pct"] if quote else None
            prev_close = quote.get("prev_close") if quote else None

            market_value = None
            pnl = None
            pnl_pct = None
            daily_pnl = None
            daily_pnl_pct = None

            if current_price is not None and prev_close and prev_close > 0:
                daily_pnl = (current_price - prev_close) * pos.quantity
                daily_pnl_pct = (current_price - prev_close) / prev_close * 100
                acc_daily_pnl += daily_pnl

            cost = pos.cost_price * pos.quantity  # all positions are in INR
            acc_cost += cost

            if current_price is not None:
                market_value = current_price * pos.quantity
                pnl = market_value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0

                acc_market_value += market_value

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "sort_order": pos.sort_order or 0,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "change_pct": change_pct,
                "market_value": round(market_value, 2) if market_value else None,
                "pnl": round(pnl, 2) if pnl else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None,
            })

        if include_quotes:
            acc_pnl = acc_market_value - acc_cost
            acc_pnl_pct = (acc_pnl / acc_cost * 100) if acc_cost > 0 else 0
            acc_total_assets = acc_market_value + acc.available_funds
        else:
            acc_pnl = 0
            acc_pnl_pct = 0
            acc_total_assets = acc.available_funds

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "total_cost": round(acc_cost, 2),
            "total_pnl": round(acc_pnl, 2),
            "total_pnl_pct": round(acc_pnl_pct, 2),
            "total_daily_pnl": round(acc_daily_pnl, 2),
            "total_assets": round(acc_total_assets, 2),
            "positions": positions_data,
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_cost
        grand_available_funds += acc.available_funds
        grand_daily_pnl += acc_daily_pnl

    if include_quotes:
        grand_pnl = grand_total_market_value - grand_total_cost
        grand_pnl_pct = (grand_pnl / grand_total_cost * 100) if grand_total_cost > 0 else 0
        grand_total_assets = grand_total_market_value + grand_available_funds
    else:
        grand_pnl = 0
        grand_pnl_pct = 0
        grand_total_assets = grand_available_funds

    # quotes dict (for the frontend stock list)
    quotes_dict = {}
    if include_quotes:
        for symbol, quote in quotes.items():
            quotes_dict[symbol] = {
                "current_price": quote.get("current_price"),
                "change_pct": quote.get("change_pct"),
            }

    return {
        "accounts": account_summaries,
        "total": {
            "total_market_value": round(grand_total_market_value, 2),
            "total_cost": round(grand_total_cost, 2),
            "total_pnl": round(grand_pnl, 2),
            "total_pnl_pct": round(grand_pnl_pct, 2),
            "total_daily_pnl": round(grand_daily_pnl, 2),
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2),
        },
        "quotes": quotes_dict,  # optional: quote data
    }


def _fetch_quotes_for_stocks(stocks: list[Stock]) -> dict:
    """Live quotes for a list of stocks."""
    if not stocks:
        return {}

    # Group by market
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]
        try:
            items = md_quote_rows(symbols, market_code.value)
            for item in items:
                quotes[item["symbol"]] = item
        except Exception as e:
            logger.error(f"Failed to get {market} quotes: {e}")

    return quotes


# Portfolio benchmark/attribution cache: rebuilding the full NAV is expensive (K-lines per stock), so results are cached by a holdings fingerprint.
# Any position change invalidates it (the fingerprint changes); failures and empty results aren't cached, so a transient fault isn't frozen for 10 minutes.
_PORTFOLIO_RESULT_CACHE = TTLCache(default_ttl_sec=600.0)


def _holdings_signature(db: Session) -> str:
    """Stable fingerprint of enabled accounts' positions (stock_id + merged quantity); DB only, no quotes or K-lines."""
    rows = (
        db.query(Position.stock_id, Position.quantity)
        .join(Account, Account.id == Position.account_id)
        .filter(Account.enabled == True)  # noqa: E712
        .all()
    )
    agg: dict[int, float] = {}
    for sid, qty in rows:
        agg[sid] = agg.get(sid, 0.0) + (qty or 0)
    return ";".join(f"{sid}:{agg[sid]:g}" for sid in sorted(agg))


def _gather_holdings(db: Session) -> list[dict]:
    """All enabled accounts' real positions as one list (INR market value / P&L + fx); the same stock across accounts is merged."""
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    stock_ids = {p.stock_id for acc in accounts for p in acc.positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for acc in accounts:
        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue
            quote = quotes.get(stock.symbol)
            price = quote.get("current_price") if quote else None
            cost_inr = pos.cost_price * pos.quantity
            mv_inr = (price * pos.quantity) if price else cost_inr
            pnl_inr = (mv_inr - cost_inr) if price else 0.0
            key = (stock.market, stock.symbol)
            if key in seen:  # same stock across accounts: merge
                h = seen[key]
                h["quantity"] += pos.quantity
                h["market_value"] += mv_inr
                h["unrealized_pnl"] += pnl_inr
            else:
                h = {
                    "symbol": stock.symbol,
                    "market": stock.market,
                    "name": stock.name,
                    "quantity": pos.quantity,
                    "fx": 1.0,
                    "market_value": mv_inr,
                    "unrealized_pnl": pnl_inr,
                    "strategy_code": pos.trading_style or "",
                }
                seen[key] = h
                out.append(h)
    return out


@router.get("/portfolio/diagnostics")
def portfolio_diagnostics(db: Session = Depends(get_db)):
    """Real portfolio diagnostics: concentration (HHI) / largest position / market split / risk notes (read-only)."""
    from src.modules.portfolio.portfolio_diagnostics import diagnose_positions

    return diagnose_positions(_gather_holdings(db))


@router.get("/portfolio/benchmark")
def portfolio_benchmark(
    days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)
):
    """Real portfolio vs benchmark: excess return / information ratio / relative drawdown + normalised NAV curve."""
    from src.modules.portfolio.portfolio_benchmark import (
        DEFAULT_BENCHMARK,
        build_portfolio_benchmark,
    )

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"empty": True, "reason": "no_holdings"}
    ckey = f"bench:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}
    res = build_portfolio_benchmark(holdings, days=days, benchmark_code=bcode)
    if not res:
        # Failures / insufficient data aren't cached, so the next round can retry (the K-line negative cache stops hammering)
        return {"empty": True, "reason": "insufficient_data"}
    _PORTFOLIO_RESULT_CACHE.set(ckey, res)
    return res


@router.get("/portfolio/todos")
def portfolio_todos(db: Session = Depends(get_db)):
    """Home-page empty-state to-dos: held with no alert / alert about to expire (actionable, even after hours)."""
    todos: list[dict] = []
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    held_ids = {p.stock_id for acc in accounts for p in acc.positions}
    if held_ids:
        ruled = {
            r.stock_id
            for r in db.query(PriceAlertRule)
            .filter(PriceAlertRule.enabled == True, PriceAlertRule.stock_id.in_(held_ids))  # noqa: E712
            .all()
        }
        for sid in held_ids - ruled:
            stock = db.query(Stock).filter(Stock.id == sid).first()
            if stock:
                todos.append(
                    {
                        "type": "no_alert",
                        "symbol": stock.symbol,
                        "market": stock.market,
                        "message": f"{stock.name} is held with no price alert",
                    }
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    expiring = (
        db.query(PriceAlertRule)
        .filter(
            PriceAlertRule.enabled == True,  # noqa: E712
            PriceAlertRule.expire_at.isnot(None),
            PriceAlertRule.expire_at >= now,
            PriceAlertRule.expire_at <= soon,
        )
        .all()
    )
    for r in expiring:
        stock = db.query(Stock).filter(Stock.id == r.stock_id).first()
        todos.append(
            {
                "type": "alert_expiring",
                "symbol": stock.symbol if stock else "",
                "market": stock.market if stock else "IN",
                "message": f"{(r.name or 'Alert')} expires soon",
            }
        )

    return {"todos": todos[:10], "count": len(todos)}


@router.get("/portfolio/attribution")
def portfolio_attribution(days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)):
    """Each position's contribution to portfolio return over the last `days` days (drags and contributors), descending."""
    from src.modules.portfolio.portfolio_benchmark import DEFAULT_BENCHMARK, build_attribution

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"items": []}
    ckey = f"attr:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"items": []}
    items = build_attribution(holdings, days=days, benchmark_code=bcode)
    result = {"items": items}
    if items:  # empty results aren't cached, so the next round can retry
        _PORTFOLIO_RESULT_CACHE.set(ckey, result)
    return result


def _gather_account_totals(db: Session, *, market_value: float) -> dict:
    """Use the same enabled-account scope as holdings; cash is stored in INR.

    Reuse the already-valued holdings instead of fetching quotes a second time.
    Non-positive equity has no meaningful exposure ratio (not zero exposure).
    """
    cash = float(db.query(func.sum(Account.available_funds)).filter(
        Account.enabled == True  # noqa: E712
    ).scalar() or 0.0)
    total = market_value + cash
    return {
        "available_funds": round(cash, 2),
        "total_assets": round(total, 2),
        "equity_ratio": market_value / total if total > 0 else None,
    }


@router.post("/portfolio/ai-review")
async def portfolio_ai_review(model_id: int | None = None, db: Session = Depends(get_db)):
    """Portfolio AI check-up: diagnostics, benchmark and attribution described neutrally (read-only, no rebalancing advice)."""
    from src.modules.portfolio.portfolio_benchmark import build_attribution, build_portfolio_benchmark
    from src.modules.portfolio.portfolio_diagnostics import diagnose_positions
    from src.platform.compliance.prompt_rules import research_rules
    from src.platform.ai.ai_failover import get_configured_failover_client
    from src.platform.compliance import ensure_guarded

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}

    diag = diagnose_positions(holdings)
    totals = _gather_account_totals(db, market_value=diag["total_market_value"])
    bench = build_portfolio_benchmark(holdings, days=60) or {}
    attr = build_attribution(holdings, days=60)
    top = attr[:3]
    worst = list(reversed(attr[-3:])) if len(attr) > 3 else []

    lines = [
        f"{diag['position_count']} positions, total market value {diag['total_market_value']:.0f}, unrealised P&L {diag['total_unrealized_pnl']:.0f}",
        f"Concentration HHI {diag['hhi']}, largest position {diag['max_weight'] * 100:.0f}% of invested amount",
        f"Enabled accounts' total assets {totals['total_assets']:.0f} INR (cash/available funds {totals['available_funds']:.0f} INR)",
        (f"Exposure: equity positions are {totals['equity_ratio'] * 100:.1f}% of total assets"
         if totals['equity_ratio'] is not None else "Exposure: total assets are not positive; ratio not computable"),
    ]
    if bench.get("excess_return") is not None:
        lines.append(
            f"Last 60 days vs {bench.get('benchmark_label', 'benchmark')}: excess {bench['excess_return']}%"
            f" (portfolio {bench.get('portfolio_return')}% / benchmark {bench.get('benchmark_return')}%),"
            f" relative drawdown {bench.get('relative_drawdown')}%"
        )
    if diag.get("by_market"):
        lines.append("Market split within holdings (market value, INR): " + ", ".join(f"{k} {v:.0f}" for k, v in diag["by_market"].items()))
    if diag.get("alerts"):
        lines.append("Risk notes: " + "; ".join(diag["alerts"]))
    if top:
        lines.append("Top contributors: " + ", ".join(f"{r['name']} ({r['contribution_pct']:+.2f}%)" for r in top))
    if worst:
        lines.append("Biggest drags: " + ", ".join(f"{r['name']} ({r['contribution_pct']:+.2f}%)" for r in worst))

    system_prompt = (
        "You write an educational portfolio check-up. Describe the portfolio using only the "
        "diagnostics, benchmark comparison and attribution provided.\n"
        + research_rules()
        + "\nDistinguish concentration within the invested amount from equity exposure "
        "relative to total assets; never describe total-asset exposure using the "
        "within-portfolio concentration figure. Cash is the available funds entered for "
        "enabled accounts and has not been verified with a broker. If total assets are not "
        "positive, do not invent an exposure ratio. Do not suggest any change to holdings, "
        "weights or cash. Output format:\n"
        "Overview: one sentence\n"
        "Observations:\n- 2 to 4 factual points, including 'Concentration within holdings' "
        "and 'Equity exposure relative to total assets'\n"
        "Main risk: one sentence"
    )
    user_content = "Portfolio overview:\n" + "\n".join(lines)
    try:
        content = await get_configured_failover_client(db, model_id).chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI health check failed: {e}")

    content = ensure_guarded(content, surface="portfolio_review")
    return {"content": content, "top": top, "worst": worst, "diagnostics": diag, "benchmark": bench, "account_totals": totals}
