import asyncio
from copy import deepcopy
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.modules.automation.research_output import parse_intraday_observation
from src.platform.compliance import (
    Feature,
    ensure_guarded,
    guard_title,
    is_feature_enabled,
    sanitize_payload,
)
from src.platform.compliance.http import feature_gate
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AgentConfig, AgentRun, LogEntry
from src.platform.scheduling.schedule_parser import preview_schedule
from src.platform.scheduling.schedule_parser import count_runs_within
from src.platform.runtime.config import Settings
from src.modules.automation.agent_catalog import (
    AGENT_KIND_CAPABILITY,
    AGENT_KIND_WORKFLOW,
    infer_agent_kind,
)
from src.modules.automation.agent_runs import ACTIVE_RUN_TTL_SEC, _as_utc

logger = logging.getLogger(__name__)

_SCAN_CACHE_LOCK = threading.Lock()
_SCAN_CACHE: dict[str, tuple[float, dict]] = {}
_SCAN_CACHE_TTL_SECONDS = {
    False: 12.0,  # quick scan
    True: 25.0,   # AI scan
}


def _build_scan_cache_key(analyze: bool, watchlist) -> str:
    symbols = sorted(f"{s.market.value}:{s.symbol}" for s in watchlist)
    return f"intraday_scan:{int(analyze)}:{'|'.join(symbols)}"


def _get_scan_cache(key: str, analyze: bool) -> dict | None:
    now = time.monotonic()
    ttl = _SCAN_CACHE_TTL_SECONDS[analyze]
    with _SCAN_CACHE_LOCK:
        hit = _SCAN_CACHE.get(key)
        if not hit:
            return None
        ts, payload = hit
        if now - ts > ttl:
            _SCAN_CACHE.pop(key, None)
            return None
        return deepcopy(payload)


def _set_scan_cache(key: str, payload: dict) -> None:
    with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[key] = (time.monotonic(), deepcopy(payload))


def _format_datetime(dt, tz: str | None = None) -> str:
    """Format a time as ISO in the app time zone.

    SQLite usually stores naive times; they are read as UTC and converted to app_timezone.
    """

    if not dt:
        return ""

    tz_name = tz or Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(tzinfo).isoformat()


def _spawn_async_run(fn, *args, name: str) -> None:
    """Run an async function in a dedicated thread."""

    def _runner():
        try:
            asyncio.run(fn(*args))
        except Exception:
            logger.exception(f"Background task failed: {name}")

    t = threading.Thread(target=_runner, name=name, daemon=True)
    t.start()


router = APIRouter()


@router.get("/health")
def agents_health(
    include_internal: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Scheduler health overview (for debugging schedules, time zones and triggers)."""
    tz = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz)
    except Exception:
        tzinfo = timezone.utc

    now = datetime.now(tzinfo)
    horizon = now + timedelta(hours=24)

    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(
            AgentConfig.kind == AGENT_KIND_WORKFLOW,
            AgentConfig.visible == True,
        )
    agents = query.order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()
    out = []
    next_24h_count = 0
    recent_failed_count = 0

    for a in agents:
        next_runs: list[str] = []
        if a.enabled and (a.schedule or "").strip():
            try:
                runs = preview_schedule(a.schedule, count=3, timezone=tz)
                next_runs = [r.isoformat() for r in runs]
                next_24h_count += count_runs_within(
                    a.schedule, start=now, end=horizon, timezone=tz
                )
            except Exception:
                next_runs = []

        last = (
            db.query(AgentRun)
            .filter(AgentRun.agent_name == a.name)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .first()
        )
        last_run = None
        if last:
            last_run = {
                "status": last.status or "",
                "created_at": _format_datetime(last.created_at, tz=tz),
                "duration_ms": last.duration_ms or 0,
                "error": last.error or "",
            }
            if a.enabled and (last.status or "") == "failed":
                recent_failed_count += 1

        out.append(
            {
                "name": a.name,
                "display_name": a.display_name,
                "kind": a.kind or infer_agent_kind(a.name),
                "visible": bool(a.visible),
                "enabled": a.enabled,
                "schedule": a.schedule or "",
                "execution_mode": a.execution_mode or "batch",
                "next_runs": next_runs,
                "last_run": last_run,
            }
        )

    return {
        "timezone": tz,
        "summary": {
            "next_24h_count": next_24h_count,
            "recent_failed_count": recent_failed_count,
        },
        "agents": out,
    }


class AgentConfigUpdate(BaseModel):
    enabled: bool | None = None
    schedule: str | None = None
    ai_model_id: int | None = None
    notify_channel_ids: list[int] | None = None
    config: dict | None = None
    visible: bool | None = None


class AgentConfigResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str
    kind: str
    visible: bool
    lifecycle_status: str
    replaced_by: str
    display_order: int
    enabled: bool
    schedule: str
    execution_mode: str  # batch / single
    ai_model_id: int | None
    notify_channel_ids: list[int]
    config: dict

    class Config:
        from_attributes = True


class AgentRunResponse(BaseModel):
    id: int
    agent_name: str
    trace_id: str = ""
    trigger_source: str = ""
    notify_attempted: bool = False
    notify_sent: bool = False
    context_chars: int = 0
    model_label: str = ""
    status: str
    result: str
    error: str
    duration_ms: int
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[AgentConfigResponse])
def list_agents(
    include_internal: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    query = db.query(AgentConfig)
    if not include_internal:
        query = query.filter(
            AgentConfig.kind == AGENT_KIND_WORKFLOW,
            AgentConfig.visible == True,
        )
    agents = query.order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc()).all()
    return [_agent_to_response(a) for a in agents]


def _agent_to_response(agent: AgentConfig) -> dict:
    kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    return {
        "id": agent.id,
        "name": agent.name,
        "display_name": agent.display_name,
        "description": agent.description,
        "kind": kind,
        "visible": bool(agent.visible),
        "lifecycle_status": agent.lifecycle_status or "active",
        "replaced_by": agent.replaced_by or "",
        "display_order": int(agent.display_order or 0),
        "enabled": agent.enabled,
        "schedule": agent.schedule or "",
        "execution_mode": agent.execution_mode or "batch",
        "ai_model_id": agent.ai_model_id,
        "notify_channel_ids": agent.notify_channel_ids or [],
        "config": agent.config or {},
    }


@router.get("/capabilities", response_model=list[AgentConfigResponse])
def list_capabilities(db: Session = Depends(get_db)):
    rows = (
        db.query(AgentConfig)
        .filter(AgentConfig.kind == AGENT_KIND_CAPABILITY)
        .order_by(AgentConfig.display_order.asc(), AgentConfig.name.asc())
        .all()
    )
    return [_agent_to_response(a) for a in rows]


@router.put("/{agent_name}", response_model=AgentConfigResponse)
def update_agent(
    agent_name: str, update: AgentConfigUpdate, db: Session = Depends(get_db)
):
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} not found")

    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(agent, key, value)

    # Capability agents run only on demand, never on a schedule.
    kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if kind == AGENT_KIND_CAPABILITY:
        agent.enabled = False
        agent.schedule = ""

    db.commit()
    db.refresh(agent)
    return _agent_to_response(agent)


@router.get("/schedule/preview")
def preview_schedule_expr(schedule: str, count: int = 5):
    """Preview the next run times of a schedule expression (scheduler time zone)."""
    tz = Settings().app_timezone or "UTC"
    if not schedule:
        return {"schedule": "", "timezone": tz, "next_runs": []}

    try:
        runs = preview_schedule(schedule, count=count, timezone=tz)
    except Exception as e:
        raise HTTPException(400, f"Could not parse the schedule: {e}")

    return {
        "schedule": schedule,
        "timezone": tz,
        "next_runs": [r.isoformat() for r in runs],
    }


@router.get("/{agent_name}/schedule/preview")
def preview_agent_schedule(
    agent_name: str, count: int = 5, db: Session = Depends(get_db)
):
    """Preview an agent's next run times (scheduler time zone)."""
    tz = Settings().app_timezone or "UTC"
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} not found")
    if not agent.schedule:
        return {"schedule": "", "timezone": tz, "next_runs": []}

    try:
        runs = preview_schedule(agent.schedule, count=count, timezone=tz)
    except Exception as e:
        raise HTTPException(400, f"Could not parse the schedule: {e}")

    return {
        "schedule": agent.schedule,
        "timezone": tz,
        "next_runs": [r.isoformat() for r in runs],
    }


@router.delete("/{agent_name}")
def delete_agent(agent_name: str, db: Session = Depends(get_db)):
    """Delete an agent configuration."""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} not found")

    # Delete the linked stock_agents rows
    from src.platform.persistence.models import StockAgent

    db.query(StockAgent).filter(StockAgent.agent_name == agent_name).delete()

    db.delete(agent)
    db.commit()
    return {"ok": True, "message": f"Agent {agent_name} deleted"}


@router.post("/{agent_name}/trigger")
async def trigger_agent_endpoint(
    agent_name: str,
    wait: bool = Query(
        default=False,
        description="Wait for the run to finish; batch agents are queued asynchronously by default",
    ),
    db: Session = Depends(get_db),
):
    """Run an agent manually."""
    agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    if not agent:
        raise HTTPException(404, f"Agent {agent_name} not found")
    agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
    if agent_kind == AGENT_KIND_WORKFLOW and not agent.enabled:
        raise HTTPException(400, f"Agent {agent_name} is not enabled")

    from server import trigger_agent

    try:
        # Batch agents can take long; allow caller to choose wait mode.
        if (
            agent_kind == AGENT_KIND_WORKFLOW
            and agent_name in {"daily_report", "premarket_outlook"}
            and not wait
        ):
            _spawn_async_run(
                trigger_agent, agent_name, name=f"trigger_agent:{agent_name}"
            )
            return {"ok": True, "queued": True, "message": "Queued to run in the background"}

        result = await trigger_agent(agent_name)
        return {"ok": True, "queued": False, "message": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Agent run failed: {e}")


@router.get("/tradingagents/running")
def find_running_for_stock(
    stock_symbol: str = Query(..., description="Stock symbol"),
    lookback_minutes: int = Query(default=30, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """Find a TradingAgents run for this stock in the last N minutes.

    When DeepAnalysisModal reopens, the backend is the source of truth for whether a run
    is in progress or just finished; more reliable than localStorage (works across
    browsers, private windows and devices).

    Logic:
    1. An unexpired running row in agent_runs (covers the data-collection stage)
    2. Otherwise the latest log_entries row with event=ta_progress and a trace_id
       containing -{symbol}-
    3. Whether that trace_id has a finished row in agent_runs
       - finished with status=success -> done (the UI can fetch the latest result)
       - finished with status=failed -> failed
       - no finished row and a log within 30 minutes -> running
        - no lifecycle row or log at all -> none

    Returns:
        {"trace_id": str|None, "status": "running"|"success"|"failed"|"none"}
    """
    # Read the persisted lifecycle row first: recovers even before any ta_progress log.
    from src.modules.automation.agent_runs import find_active_tradingagents_trace

    active_trace = find_active_tradingagents_trace(db, stock_symbol)
    if active_trace:
        return {
            "trace_id": active_trace,
            "status": "running",
            "last_activity_at": None,
        }

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    latest_log = (
        db.query(LogEntry)
        .filter(
            LogEntry.event == "ta_progress",
            LogEntry.agent_name == "tradingagents",
            LogEntry.timestamp >= cutoff,
            LogEntry.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(LogEntry.timestamp.desc())
        .first()
    )

    if not latest_log or not latest_log.trace_id:
        return {"trace_id": None, "status": "none"}

    trace_id = latest_log.trace_id

    # Has this trace finished?
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )

    # Without a run row, look at the age of the last log: past STALE_THRESHOLD it is a
    # zombie run (server restart / dead worker) and the UI may reset to idle
    status = run.status if run else "running"
    if status == "running":
        created_at = _as_utc(run.created_at) if run else None
        if created_at and (datetime.now(timezone.utc) - created_at).total_seconds() > ACTIVE_RUN_TTL_SEC:
            status = "stale"
        last_ts = latest_log.timestamp
        if status == "running" and last_ts and last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if status == "running" and last_ts:
            idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if idle_sec > 300:  # 5 minutes without progress -> stale
                status = "stale"

    return {
        "trace_id": trace_id,
        "status": status,
        "last_activity_at": _format_datetime(latest_log.timestamp),
    }


@router.get("/tradingagents/latest")
def get_tradingagents_latest(
    stock_symbol: str = Query(..., description="Stock symbol, e.g. INFY"),
    db: Session = Depends(get_db),
):
    """The latest full TradingAgents deep-analysis result for a stock (with raw_data).

    /history returns selected fields without raw_data; this endpoint exposes the full
    record (suggestion, debate_history, analyst_reports, cost_usd, ...) for the modal.
    """
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
        )
        .order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .first()
    )
    if not record:
        return None

    return {
        "id": record.id,
        "agent_name": record.agent_name,
        "stock_symbol": record.stock_symbol,
        "analysis_date": record.analysis_date,
        "title": guard_title(record.title or "", surface="ta_history_title"),
        "content": ensure_guarded(record.content, surface="ta_history"),
        "raw_data": sanitize_payload(
            record.raw_data or {}, guard_strings=True, surface="ta_history_raw"
        ),
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
    }


@router.get("/tradingagents/analysis")
def get_tradingagents_analysis(
    stock_symbol: str = Query(..., description="Stock symbol"),
    analysis_date: str = Query(..., description="Analysis date YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """A full TradingAgents deep analysis by symbol and date (for the detail page)."""
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == analysis_date,
        )
        .order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.id.desc())
        .first()
    )
    if not record:
        return None

    return {
        "id": record.id,
        "agent_name": record.agent_name,
        "stock_symbol": record.stock_symbol,
        "analysis_date": record.analysis_date,
        "title": guard_title(record.title or "", surface="ta_history_title"),
        "content": ensure_guarded(record.content, surface="ta_history"),
        "raw_data": sanitize_payload(
            record.raw_data or {}, guard_strings=True, surface="ta_history_raw"
        ),
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
    }


@router.get("/tradingagents/analysis/pdf")
def export_tradingagents_analysis_pdf(
    stock_symbol: str = Query(..., description="Stock symbol"),
    analysis_date: str = Query(..., description="Analysis date YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """Export a TradingAgents deep-analysis report as a PDF (rendered server-side, no Chromium).

    Returns application/pdf (ResponseWrapperMiddleware passes non-JSON through unwrapped).
    """
    from urllib.parse import quote

    from fastapi import HTTPException
    from fastapi.responses import Response

    from src.modules.reporting.pdf_export import assemble_report_markdown, render_analysis_pdf
    from src.platform.persistence.models import AnalysisHistory

    record = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == analysis_date,
        )
        .order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.id.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Deep analysis record not found")

    # Build the same sections as the detail page from raw_data (all four analyst reports and
    # the debate); fall back to content when raw_data is missing
    safe_raw = sanitize_payload(record.raw_data or {}, guard_strings=True, surface="pdf_raw")
    report_md = ensure_guarded(
        assemble_report_markdown(safe_raw) or (record.content or ""), surface="pdf"
    )
    pdf_bytes = render_analysis_pdf(
        guard_title(record.title or "Deep research", surface="pdf_title"), report_md
    )
    base = (record.title or f"{stock_symbol} deep analysis").replace("/", "-").replace("\\", "-").strip()
    filename = f"{base}-{analysis_date}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get(
    "/tradingagents/history-comparison",
    # Past buy/sell decisions scored against later returns are prediction tracking.
    dependencies=[Depends(feature_gate(Feature.TRADINGAGENTS_RATING))],
)
def get_tradingagents_history_comparison(
    stock_symbol: str = Query(..., description="Stock symbol, e.g. INFY"),
    market: str = Query("IN", description="Market: IN (NSE/BSE)"),
    days: int = Query(90, ge=7, le=365, description="Look-back days"),
):
    """Past TradingAgents decisions for a stock versus what the price did next.

    Returns items (each decision plus the 1d/5d/20d move) and stats (hit rate, mean return).
    A "hit": buy -> price rose / sell -> price fell / hold -> |move| < 2% (flat).
    """
    from src.modules.automation.tradingagents.operations import build_history_comparison

    return build_history_comparison(stock_symbol=stock_symbol, market=market, days=days)


@router.get("/tradingagents/budget")
def get_tradingagents_budget(db: Session = Depends(get_db)):
    """This month's TradingAgents budget usage.

    Shown in Settings and DeepAnalysisModal as "used $X of $Y".
    """
    agent = (
        db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    )
    if not agent:
        raise HTTPException(404, "The tradingagents agent is not registered")

    cfg = agent.config or {}
    monthly_budget = float(cfg.get("monthly_budget_usd", 10.0))

    # Reuse cost_tracker's SQL aggregation
    from src.modules.automation.tradingagents.observability import check_budget, estimate_cost

    budget = check_budget(monthly_budget, "tradingagents")

    # Per-run estimate (for the UI confirmation dialog)
    est = estimate_cost(
        debate_rounds=int(cfg.get("debate_rounds", 1)),
        selected_analysts=list(
            cfg.get("analyst_types", ["market", "social", "news", "fundamentals"])
        ),
        model=str(cfg.get("deep_model") or "deepseek-chat"),
    )

    return {
        **budget,
        "estimate_next_run": {
            "cost_low_usd": est["cost_low_usd"],
            "cost_high_usd": est["cost_high_usd"],
            "model": est["model"],
        },
        "over_budget_action": cfg.get("over_budget_action", "reject"),
        "enabled": bool(agent.enabled),
    }


@router.get("/runs/{trace_id}/progress")
def get_run_progress(trace_id: str, db: Session = Depends(get_db)):
    """Progress of one agent run.

    For long-running agents such as TradingAgents (3-5 minutes). Aggregates the
    event=ta_progress logs with the same trace_id in log_entries into stage progress.

    Returns:
    {
        "trace_id": ...,
        "status": "running" | "success" | "failed" | "not_found",
        "current_stage": ...,
        "completed_stages": [...],
        "elapsed_sec": float,
        "total_cost_usd": float,
        "stages": [{"name": ..., "status": "pending"|"running"|"done"}, ...],
        "run": {  # the final AgentRun (once finished)
            "status": ..., "result": ..., "error": ..., "duration_ms": ...
        }
    }
    """
    from src.modules.automation.tradingagents.observability import aggregate_progress

    if not trace_id or len(trace_id) > 64:
        raise HTTPException(400, "Invalid trace_id")

    logs = (
        db.query(LogEntry)
        .filter(
            LogEntry.trace_id == trace_id,
            LogEntry.event.in_(["ta_progress", "ta_toolkit"]),
        )
        .order_by(LogEntry.id.asc())
        .limit(500)
        .all()
    )
    log_dicts = [
        {
            "timestamp": _format_datetime(le.timestamp),
            "level": le.level,
            "message": le.message,
            "event": le.event,
            "tags": le.tags or {},
        }
        for le in logs
    ]

    progress_logs = [d for d in log_dicts if d.get("event") == "ta_progress"]
    progress = aggregate_progress(progress_logs)

    # Tool-call diagnostics: counts per action type plus the latest 50 entries.
    # Special cases fall under the base types (HIT/PASSTHROUGH/ERROR); the source field
    # names the exact origin.
    toolkit_logs = [d for d in log_dicts if d.get("event") == "ta_toolkit"]
    toolkit_summary = {"hit": 0, "miss": 0, "passthrough": 0, "fallthrough": 0, "error": 0}
    toolkit_recent = []
    for d in toolkit_logs:
        tags = d.get("tags") or {}
        action = (tags.get("action") or "").lower()
        if action in toolkit_summary:
            toolkit_summary[action] += 1
        toolkit_recent.append({
            "timestamp": d.get("timestamp"),
            "action": tags.get("action"),
            "method": tags.get("method"),
            "symbol": tags.get("symbol"),
            "reason": tags.get("reason"),
            "chars": tags.get("chars"),
            "snippet": tags.get("snippet"),
            "source": tags.get("source"),
        })
    progress["toolkit_summary"] = toolkit_summary
    progress["toolkit_recent"] = toolkit_recent[-50:]

    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )

    if run:
        status = run.status
        progress["run"] = {
            "agent_name": run.agent_name,
            "status": run.status,
            "result": (run.result or "")[:1000],
            "error": (run.error or "")[:500],
            "duration_ms": run.duration_ms,
            "model_label": run.model_label,
            "notify_sent": run.notify_sent,
        }
        if status == "running":
            created_at = _as_utc(run.created_at)
            if created_at:
                progress["started_at"] = _format_datetime(run.created_at)
                progress["elapsed_sec"] = max(
                    float(progress.get("elapsed_sec") or 0),
                    (datetime.now(timezone.utc) - created_at).total_seconds(),
                )
                if progress["elapsed_sec"] > ACTIVE_RUN_TTL_SEC:
                    status = "stale"
    elif log_dicts:
        # Detect zombie runs: after a server restart or a dead worker the logs remain but
        # nothing runs. A last progress log older than STALE_THRESHOLD means interrupted,
        # and the UI may reset to idle.
        STALE_THRESHOLD_SEC = 300  # 5 minutes
        last_log = logs[-1]  # logs are ordered by id ascending; the last is the newest
        last_ts = last_log.timestamp
        if last_ts is not None:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
            status = "stale" if idle_sec > STALE_THRESHOLD_SEC else "running"
        else:
            status = "running"
    else:
        status = "not_found"

    progress["trace_id"] = trace_id
    progress["status"] = status
    return progress


# Progress SSE: polling/push cadence and terminal-state detection
PROGRESS_SSE_POLL_SEC = 1.0
PROGRESS_SSE_MAX_DURATION_SEC = 30 * 60
PROGRESS_SSE_NOT_FOUND_GRACE_SEC = 60  # right after a trigger the logs may not exist yet
PROGRESS_TERMINAL_STATUSES = ("success", "failed", "stale")


@router.get("/runs/{trace_id}/progress/stream")
async def stream_run_progress(trace_id: str):
    """Progress SSE: the server aggregates progress and pushes each changed snapshot
    (replaces the UI's 2-second polling).

    Events:
    - progress: a full snapshot (same shape as GET .../progress) with an incrementing id;
      after a reconnect the latest snapshot is enough, no strict Last-Event-ID replay;
    - done: the run reached a terminal state (success/failed/stale), or not_found outlived
      the grace period; the stream then closes.

    GET .../progress stays as the fallback when SSE fails.
    """
    import json as _json

    from src.platform.events.sse import format_sse_comment, format_sse_event
    from src.platform.persistence.database import SessionLocal

    if not trace_id or len(trace_id) > 64:
        raise HTTPException(400, "Invalid trace_id")

    def _snapshot() -> dict:
        """Take one progress snapshot in its own session (reuses the polling aggregation)."""
        db = SessionLocal()
        try:
            return get_run_progress(trace_id, db)
        finally:
            db.close()

    async def gen():
        seq = 0
        last_payload = ""
        started = time.monotonic()
        ticks_since_push = 0
        while time.monotonic() - started < PROGRESS_SSE_MAX_DURATION_SEC:
            try:
                progress = await asyncio.to_thread(_snapshot)
            except Exception as e:
                logger.warning(f"Progress SSE snapshot failed: {e}")
                await asyncio.sleep(PROGRESS_SSE_POLL_SEC)
                continue

            payload = _json.dumps(progress, ensure_ascii=False, default=str)
            if payload != last_payload:
                last_payload = payload
                seq += 1
                ticks_since_push = 0
                yield format_sse_event(seq, "progress", payload)
            else:
                ticks_since_push += 1
                if ticks_since_push >= 15:
                    # With no change, send a heartbeat comment so proxies keep the connection open
                    ticks_since_push = 0
                    yield format_sse_comment()

            status = progress.get("status", "")
            not_found_expired = (
                status == "not_found"
                and time.monotonic() - started > PROGRESS_SSE_NOT_FOUND_GRACE_SEC
            )
            if status in PROGRESS_TERMINAL_STATUSES or not_found_expired:
                seq += 1
                yield format_sse_event(seq, "done", {"status": status})
                return

            await asyncio.sleep(PROGRESS_SSE_POLL_SEC)

        # Timeout: close the stream; the UI can reconnect or fall back to polling
        seq += 1
        yield format_sse_event(seq, "done", {"status": "timeout"})

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{agent_name}/history", response_model=list[AgentRunResponse])
def get_agent_history(agent_name: str, limit: int = 20, db: Session = Depends(get_db)):
    tz = Settings().app_timezone or "UTC"
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.agent_name == agent_name)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        AgentRunResponse(
            id=run.id,
            agent_name=run.agent_name,
            trace_id=run.trace_id or "",
            trigger_source=run.trigger_source or "",
            notify_attempted=bool(run.notify_attempted),
            notify_sent=bool(run.notify_sent),
            context_chars=int(run.context_chars or 0),
            model_label=run.model_label or "",
            status=run.status or "",
            result=run.result or "",
            error=run.error or "",
            duration_ms=run.duration_ms or 0,
            created_at=_format_datetime(run.created_at, tz=tz),
        )
        for run in runs
    ]


@router.post("/intraday/scan")
async def scan_intraday(analyze: bool = False, db: Session = Depends(get_db)):
    """
    Live scan of the stocks linked to the intraday monitor agent.

    - Only stocks with the intraday monitor enabled are scanned
    - Returns each stock's live quote and technicals
    - analyze=True also runs the AI analysis and returns structured output

    Args:
        analyze: run the AI analysis (default False)
    """
    from server import (
        load_watchlist_for_agent,
        load_portfolio_for_agent,
        build_context,
    )
    from src.platform.marketdata.marketdata_client import md_stock_data
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode, MARKETS
    from src.modules.automation.intraday_monitor import IntradayMonitorAgent
    from src.modules.research.analysis_history import get_latest_analysis, get_analysis
    from src.modules.research.context_builder import ContextBuilder
    from src.modules.research.signals import SignalPackBuilder
    from src.modules.automation.suggestion_pool import save_suggestion

    agent_name = "intraday_monitor"
    agent_cfg = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    agent_kwargs = agent_cfg.config if agent_cfg and agent_cfg.config else {}

    # Only stocks linked to the intraday monitor
    watchlist = load_watchlist_for_agent(agent_name)

    if not watchlist:
        return {
            "stocks": [],
            "message": "Enable the Intraday monitor agent for a stock first",
            "scanned_count": 0,
            "has_watchlist": False,
        }

    # Only stocks whose market is open now
    active_watchlist = [
        s for s in watchlist if MARKETS.get(s.market) and MARKETS[s.market].is_trading_time()
    ]
    if not active_watchlist:
        return {
            "stocks": [],
            "message": "The market is closed",
            "scanned_count": len(watchlist),
            "total_watchlist_count": len(watchlist),
            "skipped_not_trading_count": len(watchlist),
            "is_trading": False,
            "has_watchlist": True,
        }

    cache_key = _build_scan_cache_key(analyze, active_watchlist)
    cached = _get_scan_cache(cache_key, analyze)
    if cached is not None:
        return cached

    # Positions
    portfolio = load_portfolio_for_agent(agent_name)

    # Quotes grouped by market
    market_symbols: dict[MarketCode, list] = {}
    stock_market_map: dict[str, MarketCode] = {}
    for stock in active_watchlist:
        market_symbols.setdefault(stock.market, []).append(stock.symbol)
        stock_market_map[stock.symbol] = stock.market

    async def _fetch_market_quotes(market_code: MarketCode, symbols: list[str]):
        try:
            return await asyncio.to_thread(md_stock_data, symbols, market_code.value)
        except Exception as e:
            logger.error(f"Fetching {market_code.value} quotes failed: {e}")
            return []

    quote_batches = await asyncio.gather(
        *[
            _fetch_market_quotes(market_code, symbols)
            for market_code, symbols in market_symbols.items()
        ]
    )
    all_quotes = [q for batch in quote_batches for q in (batch or [])]
    quote_by_symbol = {q.symbol: q for q in all_quotes}

    # Agent threshold config (marks unusual moves and informs the AI)
    try:
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True, **agent_kwargs)
    except TypeError:
        # Legacy config (fall back when fields don't match)
        monitor_agent = IntradayMonitorAgent(bypass_throttle=True)

    daily_analysis = None
    premarket_analysis = None
    scan_context = None
    symbol_contexts: dict[str, dict] = {}
    quality_overview: dict = {}
    signal_packs: dict = {}
    if analyze:
        # Earlier analyses (context for the AI)
        try:
            daily_analysis = get_latest_analysis(
                agent_name="daily_report",
                stock_symbol="*",
            )
            premarket_analysis = get_analysis(
                agent_name="premarket_outlook",
                stock_symbol="*",
            )
        except Exception:
            daily_analysis = None
            premarket_analysis = None

        try:
            scan_context = build_context(agent_name)
            original_watchlist = scan_context.config.watchlist
            scan_context.config.watchlist = active_watchlist
            sym_list = [(s.symbol, s.market, s.name) for s in active_watchlist]
            signal_packs = await SignalPackBuilder().build_for_symbols(
                symbols=sym_list,
                include_news=True,
                news_hours=24,
                portfolio=portfolio,
                include_technical=True,
                include_capital_flow=True,
                include_events=True,
                events_days=3,
            )
            context_pack = await ContextBuilder().build_symbol_contexts(
                agent_name=agent_name,
                context=scan_context,
                packs=signal_packs,
                realtime_hours=6,
                extended_hours=24,
                history_days=7,
                kline_days=60,
                persist_snapshot=False,
            )
            symbol_contexts = context_pack.get("symbols", {}) or {}
            quality_overview = context_pack.get("quality_overview", {}) or {}
        except Exception as e:
            logger.warning(f"Building the intraday scan context failed; falling back to the basic analysis: {e}")
        finally:
            try:
                if scan_context:
                    scan_context.config.watchlist = original_watchlist
            except Exception:
                pass

    # Build the response
    kline_sem = asyncio.Semaphore(6)

    async def _load_kline_summary(symbol: str, market: MarketCode):
        try:
            async with kline_sem:
                return await asyncio.to_thread(
                    lambda: KlineCollector(market).get_kline_summary(symbol)
                )
        except Exception as e:
            logger.warning(f"Fetching K-lines for {symbol} failed: {e}")
            return None

    async def _build_result_item(quote):
        change_pct = quote.change_pct or 0
        market = stock_market_map.get(quote.symbol, MarketCode.IN)

        # Positions
        positions = portfolio.get_positions_for_stock(quote.symbol)
        has_position = len(positions) > 0
        cost_price = positions[0].cost_price if positions else None
        trading_style = positions[0].trading_style if positions else None
        pnl_pct = None
        if cost_price and quote.current_price:
            pnl_pct = (quote.current_price - cost_price) / cost_price * 100

        # Technicals (concurrently)
        kline_summary = await _load_kline_summary(quote.symbol, market)

        # Unusual-move type
        alert_type = None
        if abs(change_pct) >= getattr(monitor_agent, "price_alert_threshold", 3.0):
            alert_type = "sharp rise" if change_pct > 0 else "sharp fall"

        return {
            "symbol": quote.symbol,
            "name": quote.name,
            "market": market.value,
            "current_price": quote.current_price,
            "change_pct": change_pct,
            "change_amount": quote.change_amount,
            "open_price": quote.open_price,
            "high_price": quote.high_price,
            "low_price": quote.low_price,
            "prev_close": quote.prev_close,
            "volume": quote.volume,
            "turnover": quote.turnover,
            "alert_type": alert_type,
            "has_position": has_position,
            "cost_price": cost_price,
            "pnl_pct": pnl_pct,
            "trading_style": trading_style,
            "kline": kline_summary,
            "suggestion": None,  # AI output
            "context_quality": (
                (symbol_contexts.get(quote.symbol, {}) or {}).get("data_quality")
                if analyze
                else None
            ),
        }

    results = await asyncio.gather(*[_build_result_item(quote) for quote in all_quotes])

    # AI analysis
    if analyze and results:
        try:
            context = scan_context or build_context(agent_name)
            agent = monitor_agent

            ai_sem = asyncio.Semaphore(3)

            async def _analyze_item(item: dict):
                try:
                    async with ai_sem:
                        stock_data = quote_by_symbol.get(item["symbol"])
                        if not stock_data:
                            return

                        data = {
                            "stock_data": stock_data,
                            "stocks": [stock_data],
                            "kline_summary": (
                                (signal_packs.get(item["symbol"]).technical)
                                if signal_packs.get(item["symbol"])
                                else item["kline"]
                            ),
                            "signal_pack": signal_packs.get(item["symbol"]),
                            "symbol_context": symbol_contexts.get(item["symbol"], {}),
                            "quality_overview": quality_overview,
                            "daily_analysis": daily_analysis.content
                            if daily_analysis
                            else None,
                            "premarket_analysis": premarket_analysis.content
                            if premarket_analysis
                            else None,
                        }

                        # The event gate is context only; it never blocks the AI analysis.
                        # Analysis refreshes continuously; the notification layer dedupes.
                        try:
                            if getattr(agent, "event_only", False):
                                from src.modules.strategy.intraday_event_gate import check_and_update

                                decision = check_and_update(
                                    symbol=item["symbol"],
                                    change_pct=item.get("change_pct"),
                                    volume_ratio=(item.get("kline") or {}).get(
                                        "volume_ratio"
                                    ),
                                    kline_summary=item.get("kline"),
                                    price_threshold=getattr(
                                        agent, "price_alert_threshold", 3.0
                                    ),
                                    volume_threshold=getattr(
                                        agent, "volume_alert_ratio", 2.0
                                    ),
                                )
                                data["event_gate"] = {
                                    "reasons": decision.reasons,
                                    "should_analyze": bool(decision.should_analyze),
                                }
                        except Exception:
                            pass

                        system_prompt, user_content = agent.build_prompt(data, context)
                        response = await context.ai_client.chat(
                            system_prompt, user_content
                        )

                        if not is_feature_enabled(Feature.SUGGESTION_POOL):
                            # Research-only: a factual observation, never an action.
                            observation = parse_intraday_observation(response)
                            item["suggestion"] = None
                            item["observation"] = {
                                "notable": observation.notable,
                                "headline": observation.headline,
                                "observations": list(observation.observations),
                                "key_risks": list(observation.key_risks),
                            }
                            return

                        # Parse the structured output
                        suggestion = agent._parse_suggestion(response)
                        suggestion["raw"] = response.strip()[:200]

                        item["suggestion"] = suggestion
                        # Save to the suggestion pool (shown on the positions page); intraday items last 6 hours
                        expires_hours = 6
                        save_suggestion(
                            stock_symbol=item["symbol"],
                            stock_name=item["name"] or "",
                            action=suggestion.get("action", "watch"),
                            action_label=suggestion.get("action_label", "Watch"),
                            signal=suggestion.get("signal", ""),
                            reason=suggestion.get("reason", ""),
                            agent_name=agent_name,
                            agent_label=agent.display_name,
                            expires_hours=expires_hours,
                            prompt_context=user_content,
                            ai_response=response,
                            stock_market=item.get("market") or "IN",
                            meta={
                                "source": "intraday_scan",
                                "quote": {
                                    "current_price": item.get("current_price"),
                                    "change_pct": item.get("change_pct"),
                                },
                                "kline_meta": {
                                    "computed_at": (item.get("kline") or {}).get(
                                        "computed_at"
                                    ),
                                    "asof": (item.get("kline") or {}).get("asof"),
                                },
                                "event_gate": data.get("event_gate"),
                                "context_quality_score": (
                                    (data.get("symbol_context") or {})
                                    .get("data_quality", {})
                                    .get("score")
                                ),
                            },
                        )
                except Exception as e:
                    if is_feature_enabled(Feature.SUGGESTION_POOL):
                        item["suggestion"] = {
                            "action": "watch",
                            "action_label": "Watch",
                            "signal": "",
                            "reason": f"Analysis failed: {e}",
                            "should_alert": False,
                        }
                    else:
                        item["suggestion"] = None
                        item["observation"] = {
                            "notable": False,
                            "headline": "",
                            "observations": [],
                            "key_risks": [],
                            "error": "analysis failed",
                        }
                    logger.error(f"AI analysis failed for {item['symbol']}: {e}")

            await asyncio.gather(*[_analyze_item(item) for item in results])

        except Exception as e:
            logger.error(f"Building the agent context failed: {e}")

    payload = {
        "stocks": results,
        "scanned_count": len(active_watchlist),
        "total_watchlist_count": len(watchlist),
        "skipped_not_trading_count": len(watchlist) - len(active_watchlist),
        "is_trading": True,
        "has_watchlist": True,
        "available_funds": portfolio.total_available_funds,
        "quality_overview": quality_overview if analyze else {},
    }
    _set_scan_cache(cache_key, payload)
    return payload
