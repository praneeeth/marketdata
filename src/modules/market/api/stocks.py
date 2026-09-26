import asyncio
import logging
import threading
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    Stock,
    StockAgent,
    AgentConfig,
    Position,
    PriceAlertRule,
    PriceAlertHit,
)
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.models import MarketCode, MARKETS
from src.platform.marketdata.india_bridge import display_symbol, parse_symbol
from src.modules.market.brokers import get_broker_manager
from src.modules.automation.agent_catalog import AGENT_KIND_WORKFLOW, infer_agent_kind

logger = logging.getLogger(__name__)
router = APIRouter()


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: str = "IN"


class StockUpdate(BaseModel):
    name: str | None = None


class StockAgentInfo(BaseModel):
    agent_name: str
    display_name: str = ""
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockResponse(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    sort_order: int
    agents: list[StockAgentInfo] = []

    class Config:
        from_attributes = True


class StockAgentItem(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockAgentUpdate(BaseModel):
    agents: list[StockAgentItem]


class StockReorderItem(BaseModel):
    id: int
    sort_order: int


class StockReorderRequest(BaseModel):
    items: list[StockReorderItem]


def _agent_display_names(db: Session, stocks: list[Stock]) -> dict[str, str]:
    agent_names = {
        sa.agent_name
        for stock in stocks
        for sa in stock.agents
        if infer_agent_kind(sa.agent_name) == AGENT_KIND_WORKFLOW
    }
    if not agent_names:
        return {}

    rows = (
        db.query(AgentConfig.name, AgentConfig.display_name)
        .filter(AgentConfig.name.in_(agent_names))
        .all()
    )
    return {name: display_name or name for name, display_name in rows}


def _stock_to_response(stock: Stock, agent_display_names: dict[str, str] | None = None) -> dict:
    display_names = agent_display_names or {}
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "sort_order": stock.sort_order or 0,
        "agents": [
            {
                "agent_name": sa.agent_name,
                "display_name": display_names.get(sa.agent_name, sa.agent_name),
                "schedule": sa.schedule or "",
                "ai_model_id": sa.ai_model_id,
                "notify_channel_ids": sa.notify_channel_ids or [],
            }
            for sa in stock.agents
            if infer_agent_kind(sa.agent_name) == AGENT_KIND_WORKFLOW
        ],
    }


@router.get("/markets/status")
def get_market_status():
    """Trading status of each market."""
    from datetime import datetime

    result = []
    for market_code, market_def in MARKETS.items():
        try:
            now = datetime.now(market_def.get_tz())
            is_trading = market_def.is_trading_time()

            # Trading session description
            sessions_desc = []
            for session in market_def.sessions:
                sessions_desc.append(f"{session.start.strftime('%H:%M')}-{session.end.strftime('%H:%M')}")

            # Status
            weekday = now.weekday()
            current_time = now.time()

            if weekday >= 5:
                status = "closed"
                status_text = "Closed (weekend)"
            elif is_trading:
                status = "trading"
                status_text = "Open"
            else:
                # Pre-open or after close?
                first_session = market_def.sessions[0]
                last_session = market_def.sessions[-1]
                if current_time < first_session.start:
                    status = "pre_market"
                    status_text = "Pre-open"
                elif current_time > last_session.end:
                    status = "after_hours"
                    status_text = "Closed"
                else:
                    status = "break"
                    status_text = "Lunch break"

            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": status,
                "status_text": status_text,
                "is_trading": is_trading,
                "sessions": sessions_desc,
                "local_time": now.strftime("%H:%M"),
                "timezone": market_def.timezone,
            })
        except Exception as e:
            # One market failing doesn't affect the others
            logger.error(f"Failed to get the {market_code.value} market status: {e}")
            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": "unknown",
                "status_text": "Unknown",
                "is_trading": False,
                "sessions": [],
                "local_time": "--:--",
                "timezone": market_def.timezone,
                "error": str(e),
            })

    return result


@router.get("/search")
def search(
    q: str = Query("", min_length=1), market: str = Query(""), db: Session = Depends(get_db)
):
    """Search NSE/BSE stocks and indices in the user's broker instrument list.

    Without a broker instrument list (nothing connected, or only the dev Yahoo source) the
    typed symbol itself is offered, marked unverified, so stocks can still be added.
    """
    results = get_broker_manager().search_instruments(db, q)
    if results:
        return results
    import re

    try:
        ref = parse_symbol(q)
    except ValueError:
        return []
    symbol = display_symbol(ref)
    if not re.match(MARKETS[MarketCode.IN].symbol_pattern, symbol):
        return []
    return [{
        "symbol": symbol,
        "name": f"{ref.tradingsymbol} (unverified: connect a broker to search)",
        "market": "IN",
        "exchange": ref.exchange.value,
        "unverified": True,
    }]


@router.get("", response_model=list[StockResponse])
def list_stocks(db: Session = Depends(get_db)):
    stocks = db.query(Stock).order_by(Stock.sort_order.asc(), Stock.id.asc()).all()
    agent_display_names = _agent_display_names(db, stocks)
    return [_stock_to_response(s, agent_display_names) for s in stocks]


@router.get("/quotes")
def get_quotes(db: Session = Depends(get_db)):
    """Live quotes for every watchlist stock."""
    stocks = db.query(Stock).all()
    if not stocks:
        return {}

    # Group by market
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            MarketCode(market)  # validate the market
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]   # raw symbols; md formats them per market
        try:
            items = md_quote_rows(symbols, market)
            for item in items:
                quotes[item["symbol"]] = {
                    "current_price": item["current_price"],
                    "change_pct": item["change_pct"],
                    "change_amount": item["change_amount"],
                    "prev_close": item["prev_close"],
                    # India only: which broker served it and whether it is official data.
                    **({"source": item["source"], "quality": item["quality"]}
                       if "quality" in item else {}),
                }
        except Exception as e:
            logger.error(f"Failed to get {market} quotes: {e}")

    return quotes


def _normalise_india_stock(stock: StockCreate) -> StockCreate:
    """Canonical India symbols: "INFY" for NSE, "BSE:INFY" for BSE."""
    import re

    try:
        ref = parse_symbol(stock.symbol)
    except ValueError:
        raise HTTPException(400, "Enter a symbol, e.g. INFY or BSE:500209") from None
    symbol = display_symbol(ref)
    if not re.match(MARKETS[MarketCode.IN].symbol_pattern, symbol):
        raise HTTPException(400, f"{stock.symbol!r} is not a valid NSE/BSE symbol")
    name = (stock.name or "").strip()
    if not name or "(unverified" in name:
        name = ref.tradingsymbol
    return StockCreate(symbol=symbol, name=name, market="IN")


@router.post("", response_model=StockResponse)
def create_stock(stock: StockCreate, db: Session = Depends(get_db)):
    if stock.market.upper() == "IN":
        stock = _normalise_india_stock(stock)
    existing = db.query(Stock).filter(
        Stock.symbol == stock.symbol, Stock.market == stock.market
    ).first()
    if existing:
        raise HTTPException(400, f"Stock {stock.symbol} already exists")

    max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
    db_stock = Stock(**stock.model_dump(), sort_order=int(max_order) + 1)
    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.put("/reorder")
def reorder_stocks(body: StockReorderRequest, db: Session = Depends(get_db)):
    if not body.items:
        return {"updated": 0}
    ids = [int(x.id) for x in body.items]
    rows = db.query(Stock).filter(Stock.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in body.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


@router.put("/{stock_id}", response_model=StockResponse)
def update_stock(stock_id: int, stock: StockUpdate, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "Stock not found")

    for key, value in stock.model_dump(exclude_unset=True).items():
        setattr(db_stock, key, value)

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.delete("/{stock_id}")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "Stock not found")

    # Before deleting a stock, its positions must be removed first so asset data isn't deleted by mistake.
    has_position = db.query(Position.id).filter(Position.stock_id == stock_id).first()
    if has_position:
        raise HTTPException(400, "This stock has positions; delete them before deleting the stock")

    # SQLite may not enable FK cascades by default, so alert data is cleaned up by hand to avoid orphan rows.
    rule_ids = [
        row[0]
        for row in db.query(PriceAlertRule.id).filter(
            PriceAlertRule.stock_id == stock_id
        ).all()
    ]
    if rule_ids:
        db.query(PriceAlertHit).filter(PriceAlertHit.rule_id.in_(rule_ids)).delete(
            synchronize_session=False
        )
    db.query(PriceAlertHit).filter(PriceAlertHit.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(PriceAlertRule).filter(PriceAlertRule.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete(
        synchronize_session=False
    )

    db.delete(db_stock)
    db.commit()
    return {"ok": True}


@router.put("/{stock_id}/agents", response_model=StockResponse)
def update_stock_agents(stock_id: int, body: StockAgentUpdate, db: Session = Depends(get_db)):
    """Update the agents linked to a stock (with schedule config and AI/notification overrides)."""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "Stock not found")

    for item in body.agents:
        agent = db.query(AgentConfig).filter(AgentConfig.name == item.agent_name).first()
        if not agent:
            raise HTTPException(400, f"Agent {item.agent_name} not found")
        agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
        if agent_kind != AGENT_KIND_WORKFLOW:
            raise HTTPException(400, f"Agent {item.agent_name} is an internal capability and can't be linked to a stock")

    # Clear the old links and rebuild
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete()
    for item in body.agents:
        db.add(StockAgent(
            stock_id=stock_id,
            agent_name=item.agent_name,
            schedule=item.schedule,
            ai_model_id=item.ai_model_id,
            notify_channel_ids=item.notify_channel_ids,
        ))

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.post("/{stock_id}/agents/{agent_name}/trigger")
async def trigger_stock_agent(
    stock_id: int,
    agent_name: str,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    allow_unbound: bool = False,
    wait: bool = False,
    force_refresh: bool = False,
    symbol: str = Query(""),
    market: str = Query("IN"),
    name: str = Query(""),
    db: Session = Depends(get_db),
):
    """Run an agent for one stock manually.

    - Normal mode: pass a valid stock_id
    - Unbound mode: stock_id<=0 plus symbol/market (needs allow_unbound=true)
    - Unbound mode turns notifications off by default (analysis only)
    - Runs asynchronously by default (returns at once); pass wait=true to wait for the result
    """
    sa = None
    trigger_stock = None
    suppress_notify = stock_id <= 0

    if stock_id > 0:
        db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not db_stock:
            raise HTTPException(404, "Stock not found")

        sa = db.query(StockAgent).filter(
            StockAgent.stock_id == stock_id, StockAgent.agent_name == agent_name
        ).first()
        if not sa and not allow_unbound:
            raise HTTPException(400, f"Stock isn't linked to agent {agent_name}")
        if not sa and allow_unbound:
            # When unbound runs are allowed, at least make sure the agent exists.
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} not found")
        trigger_stock = db_stock
    else:
        symbol = (symbol or "").strip()
        if not symbol:
            raise HTTPException(400, "symbol is required when stock_id<=0")
        if not allow_unbound:
            raise HTTPException(400, "allow_unbound=true is required when stock_id<=0")

        market = (market or "IN").strip().upper() or "IN"
        name = (name or "").strip() or symbol
        db_stock = db.query(Stock).filter(
            Stock.symbol == symbol, Stock.market == market
        ).first()
        if db_stock:
            sa = db.query(StockAgent).filter(
                StockAgent.stock_id == db_stock.id, StockAgent.agent_name == agent_name
            ).first()
            trigger_stock = db_stock
        else:
            # Not saved: a one-off analysis from the details dialog for a stock that isn't held or watched.
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} not found")
            trigger_stock = SimpleNamespace(
                id=0,
                symbol=symbol,
                name=name,
                market=market,
            )

    logger.info(
        f"Manual agent run {agent_name} - {trigger_stock.name} ({trigger_stock.symbol})"
    )

    from server import trigger_agent_for_stock
    import time as _time

    # Idempotency guard: one TradingAgents run takes 3-5 minutes, and a double click could start the same symbol twice.
    # The backend first checks for a TA run really in progress for this symbol and returns its trace_id (no new run).
    # force_refresh=true skips the check so the user can force a re-run (the old run ends naturally; new trace_id).
    if agent_name == "tradingagents" and not force_refresh:
        from src.modules.automation import find_active_tradingagents_trace
        existing_trace = find_active_tradingagents_trace(db, trigger_stock.symbol)
        if existing_trace:
            logger.info(
                f"[trigger idempotency] {trigger_stock.symbol} already has a run in progress trace={existing_trace}; "
                f"reusing it instead of starting a new one"
            )
            return {
                "queued": False,
                "trace_id": existing_trace,
                "message": "A deep research run is already in progress; returning its progress",
                "deduplicated": True,
            }

    # Generate the trace_id up front; the frontend polls progress with it
    trace_id = f"man-{agent_name}-{trigger_stock.symbol}-{int(_time.time() * 1000)}"

    # Save the lifecycle first so a page refresh can recover the run even before the background thread logs progress.
    if agent_name == "tradingagents":
        try:
            from src.modules.automation.agent_runs import start_agent_run
            start_agent_run(
                agent_name=agent_name,
                trace_id=trace_id,
                trigger_source="manual",
            )
        except Exception as e:
            logger.warning(f"[TA] Failed to write the running lifecycle; continuing: {e}")

    # Write a "run started" progress entry right away so the frontend's first poll sees running.
    # Otherwise trigger_agent_for_stock first awaits agent.collect() (fetching data can take
    # 30s+), with no ta_progress entries meanwhile -> the progress endpoint returns not_found
    # -> after the 60s grace period the frontend resets to idle, looking like "progress stalled and reset".
    if agent_name == "tradingagents":
        try:
            from src.platform.observability.log_context import log_context
            with log_context(
                trace_id=trace_id,
                agent_name="tradingagents",
                event="ta_progress",
                tags={"stage": "task_triggered", "action": "triggered"},
            ):
                logger.info(
                    f"[TA] Run started - {trigger_stock.symbol} (trace={trace_id})"
                )
        except Exception as e:
            logger.warning(f"[TA] Failed to write the start log; continuing: {e}")

    if not wait:
        # Async mode: run in the background and return at once
        sa_id = sa.id if sa else None

        def _runner():
            try:
                asyncio.run(trigger_agent_for_stock(
                    agent_name,
                    trigger_stock,
                    stock_agent_id=sa_id,
                    bypass_throttle=bypass_throttle,
                    bypass_market_hours=bypass_market_hours,
                    suppress_notify=suppress_notify,
                    trace_id=trace_id,
                    force_refresh=force_refresh,
                ))
                logger.info(f"Agent {agent_name} finished in the background - {trigger_stock.symbol}")
            except Exception:
                logger.exception(f"Agent {agent_name} failed in the background - {trigger_stock.symbol}")

        t = threading.Thread(
            target=_runner,
            name=f"stock-trigger-{agent_name}-{trigger_stock.symbol}",
            daemon=True,
        )
        t.start()
        return {"queued": True, "trace_id": trace_id, "message": "Queued to run in the background"}

    # Sync mode: wait for the result
    try:
        result = await trigger_agent_for_stock(
            agent_name,
            trigger_stock,
            stock_agent_id=sa.id if sa else None,
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
            suppress_notify=suppress_notify,
            trace_id=trace_id,
            force_refresh=force_refresh,
        )
        logger.info(f"Agent {agent_name} finished - {trigger_stock.symbol}")
        return {
            "result": result,
            "trace_id": trace_id,
            "code": int(result.get("code", 0)),
            "success": bool(result.get("success", True)),
            "message": result.get("message", "ok"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Agent {agent_name} failed - {trigger_stock.symbol}: {e}")
        raise HTTPException(500, f"Agent run failed: {e}")
