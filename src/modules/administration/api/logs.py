"""Log centre API."""
import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from pydantic import BaseModel
from sqlalchemy.orm import Session

# An endpoint has a query parameter named logger, so the module-level logger uses an alias to avoid shadowing
_module_logger = logging.getLogger(__name__)

from src.platform.persistence.database import get_db
from src.platform.persistence.models import LogEntry
from src.platform.observability.log_handler import get_log_handler_stats


def _format_datetime(dt) -> str:
    """Format a time as an ISO string with a time zone."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


router = APIRouter()

INFRA_LOGGER_PREFIXES = (
    "httpx",
    "httpcore",
    "urllib3",
    "uvicorn.access",
    "sqlalchemy.engine",
)


def _infra_logger_expr():
    return or_(*[LogEntry.logger_name.startswith(p) for p in INFRA_LOGGER_PREFIXES])


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class LogEntryResponse(BaseModel):
    id: int
    timestamp: str
    level: str
    logger_name: str
    message: str
    trace_id: str = ""
    run_id: str = ""
    agent_name: str = ""
    event: str = ""
    tags: dict | None = None
    notify_status: str = ""
    notify_reason: str = ""

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    items: list[LogEntryResponse]
    total: int
    has_more: bool = False
    next_before_id: int | None = None


def _apply_log_filters(
    query,
    *,
    level: str = "",
    q: str = "",
    logger: str = "",
    trace_id: str = "",
    run_id: str = "",
    agent_name: str = "",
    event: str = "",
    notify_status: str = "",
    domain: str = "all",
    since: str = "",
    until: str = "",
):
    """Apply the query filters to a LogEntry query (shared by the list and the SSE tail)."""
    if level:
        levels = [l.strip().upper() for l in level.split(",") if l.strip()]
        if levels:
            query = query.filter(LogEntry.level.in_(levels))

    if q:
        query = query.filter(
            or_(
                LogEntry.message.contains(q),
                LogEntry.logger_name.contains(q),
                LogEntry.trace_id.contains(q),
                LogEntry.agent_name.contains(q),
                LogEntry.event.contains(q),
            )
        )

    if logger:
        parts = [p.strip() for p in logger.split(",") if p.strip()]
        if len(parts) == 1:
            query = query.filter(LogEntry.logger_name.contains(parts[0]))
        elif parts:
            query = query.filter(or_(*[LogEntry.logger_name.contains(p) for p in parts]))

    if trace_id:
        query = query.filter(LogEntry.trace_id == trace_id)
    if run_id:
        query = query.filter(LogEntry.run_id == run_id)
    if agent_name:
        query = query.filter(LogEntry.agent_name == agent_name)
    if event:
        parts = [p.strip() for p in event.split(",") if p.strip()]
        if len(parts) == 1:
            query = query.filter(LogEntry.event == parts[0])
        elif parts:
            query = query.filter(LogEntry.event.in_(parts))
    if notify_status:
        query = query.filter(LogEntry.notify_status == notify_status)

    domain_norm = (domain or "all").strip().lower()
    infra_expr = _infra_logger_expr()
    if domain_norm == "business":
        query = query.filter(~infra_expr)
    elif domain_norm == "infra":
        query = query.filter(infra_expr)

    if since:
        try:
            since_dt = _parse_iso(since)
            if since_dt is None:
                raise ValueError("invalid since")
            query = query.filter(LogEntry.timestamp >= since_dt)
        except ValueError:
            pass

    if until:
        try:
            until_dt = _parse_iso(until)
            if until_dt is None:
                raise ValueError("invalid until")
            query = query.filter(LogEntry.timestamp <= until_dt)
        except ValueError:
            pass

    return query


def _to_log_response(item: LogEntry) -> LogEntryResponse:
    """Convert an ORM row into the response model (shared by the list and the SSE tail)."""
    return LogEntryResponse(
        id=item.id,
        timestamp=_format_datetime(item.timestamp),
        level=item.level,
        logger_name=item.logger_name or "",
        message=item.message or "",
        trace_id=item.trace_id or "",
        run_id=item.run_id or "",
        agent_name=item.agent_name or "",
        event=item.event or "",
        tags=item.tags or {},
        notify_status=item.notify_status or "",
        notify_reason=item.notify_reason or "",
    )


@router.get("", response_model=LogListResponse)
def list_logs(
    level: str = Query("", description="Log level filter, comma-separated"),
    q: str = Query("", description="Keyword search"),
    logger: str = Query("", description="Logger name filter"),
    trace_id: str = Query("", description="Trace ID"),
    run_id: str = Query("", description="Run ID"),
    agent_name: str = Query("", description="Agent name filter"),
    event: str = Query("", description="Event filter"),
    notify_status: str = Query("", description="Notification status filter: attempted/skipped/sent/failed"),
    domain: str = Query("all", description="Log domain: all/business/infra"),
    since: str = Query("", description="Start time, ISO format"),
    until: str = Query("", description="End time, ISO format"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    before_id: int = Query(0, ge=0, description="Cursor pagination: logs before this id"),
    db: Session = Depends(get_db),
):
    query = _apply_log_filters(
        db.query(LogEntry),
        level=level,
        q=q,
        logger=logger,
        trace_id=trace_id,
        run_id=run_id,
        agent_name=agent_name,
        event=event,
        notify_status=notify_status,
        domain=domain,
        since=since,
        until=until,
    )

    total = query.count()
    has_more = False
    next_before_id = None

    if before_id > 0:
        rows = (
            query.filter(LogEntry.id < before_id)
            .order_by(LogEntry.id.desc())
            .limit(limit + 1)
            .all()
        )
        if len(rows) > limit:
            has_more = True
            rows = rows[:limit]
        if rows:
            next_before_id = rows[-1].id
        items = rows
    else:
        items = (
            query.order_by(LogEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        has_more = (offset + len(items)) < total
        if items:
            next_before_id = items[-1].id

    return LogListResponse(
        items=[_to_log_response(item) for item in items],
        total=total,
        has_more=has_more,
        next_before_id=next_before_id,
    )


# Poll/push rhythm of the log SSE tail
LOGS_SSE_POLL_SEC = 2.0
LOGS_SSE_MAX_DURATION_SEC = 30 * 60
LOGS_SSE_BATCH_LIMIT = 200


@router.get("/stream")
async def stream_logs(
    request: Request,
    level: str = Query("", description="Log level filter, comma-separated"),
    q: str = Query("", description="Keyword search"),
    logger_name: str = Query("", alias="logger", description="Logger name filter"),
    domain: str = Query("all", description="Log domain: all/business/infra"),
    since: str = Query("", description="Start time, ISO format"),
    last_event_id: int = Query(0, ge=0, description="Last log id received before the disconnect"),
):
    """Log SSE tail: keeps pushing new logs matching the filters (replaces the frontend's 3s polling).

    - The event id is the log row id (naturally increasing), so a reconnect with Last-Event-ID
      (header first, query as fallback) resumes from the gap;
    - a first connection (no Last-Event-ID) starts at the current latest id and pushes only new rows;
      existing rows come from the GET /api/logs list endpoint (kept as is, as the fallback).
    """
    from src.platform.events.sse import format_sse_comment, format_sse_event
    from src.platform.persistence.database import SessionLocal

    header_id = request.headers.get("last-event-id", "")
    resume_id = int(header_id) if header_id.isdigit() else last_event_id

    def _fetch_after(cursor: int) -> list[LogEntryResponse]:
        """Open a separate session for new logs with id > cursor (ascending, capped to absorb bursts)."""
        db = SessionLocal()
        try:
            query = _apply_log_filters(
                db.query(LogEntry),
                level=level,
                q=q,
                logger=logger_name,
                domain=domain,
                since=since,
            )
            rows = (
                query.filter(LogEntry.id > cursor)
                .order_by(LogEntry.id.asc())
                .limit(LOGS_SSE_BATCH_LIMIT)
                .all()
            )
            return [_to_log_response(r) for r in rows]
        finally:
            db.close()

    def _current_max_id() -> int:
        db = SessionLocal()
        try:
            row = db.query(LogEntry.id).order_by(LogEntry.id.desc()).first()
            return int(row[0]) if row else 0
        finally:
            db.close()

    async def gen():
        # With Last-Event-ID -> resume from the gap; otherwise tail only new rows from the current latest
        cursor = resume_id if resume_id > 0 else await asyncio.to_thread(_current_max_id)
        started = time.monotonic()
        idle_ticks = 0
        while time.monotonic() - started < LOGS_SSE_MAX_DURATION_SEC:
            try:
                items = await asyncio.to_thread(_fetch_after, cursor)
            except Exception as e:
                _module_logger.warning(f"Log SSE query failed: {e}")
                await asyncio.sleep(LOGS_SSE_POLL_SEC)
                continue

            if items:
                cursor = items[-1].id
                idle_ticks = 0
                yield format_sse_event(
                    cursor, "logs", {"items": [i.model_dump() for i in items]}
                )
                # A full batch means there's a backlog; fetch again at once
                if len(items) >= LOGS_SSE_BATCH_LIMIT:
                    continue
            else:
                idle_ticks += 1
                if idle_ticks >= 8:
                    idle_ticks = 0
                    yield format_sse_comment()

            await asyncio.sleep(LOGS_SSE_POLL_SEC)

        yield format_sse_event(cursor + 1, "done", {"status": "timeout"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("")
def clear_logs(db: Session = Depends(get_db)):
    count = db.query(LogEntry).delete()
    db.commit()
    return {"deleted": count}


@router.get("/meta")
def logs_meta(
    domain: str = Query("all", description="Log domain: all/business/infra"),
    since: str = Query("", description="Start time, ISO format"),
    db: Session = Depends(get_db),
):
    query = db.query(LogEntry)

    domain_norm = (domain or "all").strip().lower()
    infra_expr = _infra_logger_expr()
    if domain_norm == "business":
        query = query.filter(~infra_expr)
    elif domain_norm == "infra":
        query = query.filter(infra_expr)

    if since:
        try:
            since_dt = _parse_iso(since)
            if since_dt is None:
                raise ValueError("invalid since")
            query = query.filter(LogEntry.timestamp >= since_dt)
        except ValueError:
            pass

    total = query.count()
    level_dist = (
        query.with_entities(LogEntry.level, func.count(LogEntry.id))
        .group_by(LogEntry.level)
        .all()
    )
    logger_dist = (
        query.with_entities(LogEntry.logger_name, func.count(LogEntry.id))
        .group_by(LogEntry.logger_name)
        .order_by(func.count(LogEntry.id).desc())
        .limit(30)
        .all()
    )
    event_dist = (
        query.with_entities(LogEntry.event, func.count(LogEntry.id))
        .filter(LogEntry.event != "")
        .group_by(LogEntry.event)
        .order_by(func.count(LogEntry.id).desc())
        .limit(20)
        .all()
    )

    return {
        "total": total,
        "levels": {k: int(v) for k, v in level_dist if k},
        "top_loggers": [
            {"logger_name": k or "", "count": int(v)} for k, v in logger_dist if k
        ],
        "top_events": [{"event": k or "", "count": int(v)} for k, v in event_dist if k],
    }


@router.get("/health")
def logs_health(db: Session = Depends(get_db)):
    total = db.query(LogEntry).count()
    infra_expr = _infra_logger_expr()
    infra_count = db.query(LogEntry).filter(infra_expr).count()
    business_count = max(total - infra_count, 0)
    oldest = db.query(LogEntry).order_by(LogEntry.id.asc()).first()
    newest = db.query(LogEntry).order_by(LogEntry.id.desc()).first()
    return {
        "storage": {
            "total": total,
            "business_count": business_count,
            "infra_count": infra_count,
            "oldest": _format_datetime(oldest.timestamp) if oldest else "",
            "newest": _format_datetime(newest.timestamp) if newest else "",
        },
        "writer": get_log_handler_stats(),
    }
