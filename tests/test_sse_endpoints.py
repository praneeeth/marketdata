"""Unit tests for the progress SSE and log SSE tail endpoints (mocked snapshots / in-memory DB; no real tasks)."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.automation.api.agents as agents_api
import src.modules.administration.api.logs as logs_api
from src.platform.persistence.database import Base
from src.platform.persistence.models import LogEntry


def _parse_events(body: str) -> list[tuple[str, dict]]:
    """Parse SSE wire text into [(event, data), ...], skipping heartbeat comments."""
    events = []
    for block in body.split("\n\n"):
        lines = [l for l in block.split("\n") if l]
        if not lines or lines[0].startswith(":"):
            continue
        event = next((l.split(": ", 1)[1] for l in lines if l.startswith("event: ")), "")
        data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
        events.append((event, json.loads(data_raw) if data_raw else {}))
    return events


async def _drain(resp) -> str:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_progress_sse_push_and_done(monkeypatch):
    """Progress SSE: progress events only when the snapshot changes; done after the final state, then the stream closes."""
    snapshots = [
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},  # unchanged, not pushed
        {"trace_id": "t1", "status": "running", "current_stage": "trader"},
        {"trace_id": "t1", "status": "success", "current_stage": None},
    ]
    calls = {"n": 0}

    def fake_get_run_progress(trace_id, db):
        idx = min(calls["n"], len(snapshots) - 1)
        calls["n"] += 1
        return snapshots[idx]

    monkeypatch.setattr(agents_api, "get_run_progress", fake_get_run_progress)
    monkeypatch.setattr(agents_api, "PROGRESS_SSE_POLL_SEC", 0.01)

    async def run():
        resp = await agents_api.stream_run_progress("t1")
        assert resp.media_type == "text/event-stream"
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    kinds = [e for e, _ in events]
    # Only 3 distinct payloads among 4 snapshots -> 3 progress + 1 done
    assert kinds == ["progress", "progress", "progress", "done"]
    assert events[0][1]["current_stage"] == "market_analyst"
    assert events[2][1]["status"] == "success"
    assert events[3][1]["status"] == "success"


def test_progress_sse_invalid_trace_id():
    """Progress SSE: an invalid trace_id returns 400."""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(agents_api.stream_run_progress("x" * 65))
    assert ei.value.status_code == 400


def _make_log_db(monkeypatch):
    """In-memory SQLite + seeded log rows, replacing SessionLocal."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("src.platform.persistence.database.SessionLocal", factory)

    db = factory()
    for level, msg in [("INFO", "startup complete"), ("ERROR", "quote fetch failed"), ("INFO", "scheduled run")]:
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level=level,
            logger_name="panwatch.test",
            message=msg,
        ))
    db.commit()
    db.close()
    return factory


def test_logs_sse_resume_from_last_event_id(monkeypatch):
    """Log SSE: with Last-Event-ID it resumes from the gap; the event id is the log row id."""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    ids = [item["id"] for item in logs_events[0]["items"]]
    assert ids == [2, 3]
    # The event id is the last log id
    assert "id: 3\nevent: logs\n" in body
    # A done event closes it after the timeout
    assert events[-1][0] == "done"


def test_logs_sse_filters(monkeypatch):
    """Log SSE: the level filter works; only matching rows are pushed."""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="ERROR", q="", logger_name="", domain="all", since="", last_event_id=1,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    items = logs_events[0]["items"]
    assert len(items) == 1
    assert items[0]["level"] == "ERROR"
    assert items[0]["message"] == "quote fetch failed"


def test_logs_sse_tail_only_new(monkeypatch):
    """Log SSE: without Last-Event-ID it starts at the current latest and only tails new rows."""
    factory = _make_log_db(monkeypatch)
    # Timing is relaxed to tolerate load: this case depends on "baseline snapshot first, then new rows".
    # The original 0.05s margin could let the insert happen before the first baseline poll finished under heavy load,
    # folding the new row into the baseline so it wasn't tailed (occasional flakiness unrelated to the change). A larger
    # MAX_DURATION and wait before inserting give the event loop enough scheduling room.
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.02)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 1.5)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )

        received: list[str] = []

        async def consume():
            async for chunk in resp.body_iterator:
                received.append(chunk)

        task = asyncio.create_task(consume())
        # Insert the new log only after the first baseline poll has safely finished (relaxed to 0.3s against load jitter)
        await asyncio.sleep(0.3)
        db = factory()
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level="WARNING",
            logger_name="panwatch.test",
            message="new log entry",
        ))
        db.commit()
        db.close()
        await asyncio.wait_for(task, timeout=3)
        return "".join(received)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    # Only the newly inserted row; the 3 existing rows aren't replayed
    assert len(logs_events) == 1
    assert [i["message"] for i in logs_events[0]["items"]] == ["new log entry"]
