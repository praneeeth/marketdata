"""Agent run records, written to the agent_runs table (for the UI)."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AgentRun, LogEntry

logger = logging.getLogger(__name__)

# The collection stage may have no progress logs for a while when external sources rate-limit/retry, so the
# "stale after 5 minutes without logs" rule can't apply; but after a restart old runs can't be resumed forever either.
ACTIVE_RUN_TTL_SEC = 45 * 60


def _as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def start_agent_run(
    agent_name: str,
    trace_id: str,
    trigger_source: str = "",
    model_label: str = "",
) -> None:
    """Write the running lifecycle record before the run really starts.

    The same trace may be written from both the API wrapper and the run entry point, so the write is idempotent.
    """
    if not trace_id:
        return
    db = SessionLocal()
    try:
        existing = (
            db.query(AgentRun)
            .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
            .order_by(AgentRun.id.desc())
            .first()
        )
        if existing:
            return
        db.add(AgentRun(
            agent_name=agent_name,
            status="running",
            trace_id=trace_id[:64],
            trigger_source=(trigger_source or "")[:32],
            model_label=(model_label or "")[:255],
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to write AgentRun running state: {e}")
        db.rollback()
    finally:
        db.close()


def record_agent_run(
    agent_name: str,
    status: str,
    result: str = "",
    error: str = "",
    duration_ms: int = 0,
    trace_id: str = "",
    trigger_source: str = "",
    notify_attempted: bool = False,
    notify_sent: bool = False,
    context_chars: int = 0,
    model_label: str = "",
) -> None:
    """Record one agent run result in the database.

    Args:
        agent_name: agent name
        status: success / failed
        result: short result (truncated)
        error: error message (truncated)
        duration_ms: run time (milliseconds)
        trace_id: trace id of the run
        trigger_source: schedule / manual / api
        notify_attempted: whether a notification was attempted
        notify_sent: whether the notification was sent
        context_chars: prompt/context character count
        model_label: model used for this run
    """
    db = SessionLocal()
    try:
        existing = None
        if trace_id:
            existing = (
                db.query(AgentRun)
                .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
                .order_by(AgentRun.id.desc())
                .first()
            )
        values = {
            "agent_name": agent_name,
            "status": status,
            "trace_id": (trace_id or "")[:64],
            "trigger_source": (trigger_source or "")[:32],
            "notify_attempted": bool(notify_attempted),
            "notify_sent": bool(notify_sent),
            "context_chars": max(0, int(context_chars or 0)),
            "model_label": (model_label or "")[:255],
            "result": (result or "")[:2000],
            "error": (error or "")[:2000],
            "duration_ms": duration_ms,
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(AgentRun(**values))
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to write AgentRun: {e}")
        db.rollback()
    finally:
        db.close()


def find_active_tradingagents_trace(db: Session, stock_symbol: str) -> str | None:
    """Return a symbol's TradingAgents trace that is still running, for idempotent triggering across modules.

    Run state belongs to the automation module; the market module may only use this public query to decide whether
    to start a new run, and must not import the automation HTTP router or query its internals.
    """
    now = datetime.now(timezone.utc)

    # The lifecycle record is the preferred source: it can recover a run even before any ta_progress in the collection stage,
    # and won't re-trigger a run just because an external source logged nothing for 5 minutes.
    active_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.agent_name == "tradingagents",
            AgentRun.status == "running",
            AgentRun.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .first()
    )
    if active_run and active_run.trace_id:
        created_at = _as_utc(active_run.created_at)
        if created_at is None or (now - created_at).total_seconds() <= ACTIVE_RUN_TTL_SEC:
            return active_run.trace_id
        # Past the whole run's safety window, old logs can't mark it running again.
        return None

    cutoff = now - timedelta(minutes=30)
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
        return None

    trace_id = latest_log.trace_id
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )
    if run and run.status in ("success", "failed"):
        return None

    last_ts = _as_utc(latest_log.timestamp)
    if last_ts and (now - last_ts).total_seconds() > 300:
        return None
    return trace_id
