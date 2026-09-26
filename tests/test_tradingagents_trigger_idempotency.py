"""Trigger API idempotency + state machine fallback tests.

Three principles:
1. after a task restarts (stale), a new analysis may be triggered
2. each trigger first checks for a task really running and, if there is one, returns its trace_id without starting a new one
3. force_refresh=true always starts a new task (allowing "ignore the cache and re-analyse")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.modules.automation import find_active_tradingagents_trace


def _fake_log(timestamp, trace_id="man-tradingagents-601127-1234"):
    le = MagicMock()
    le.id = 1
    le.timestamp = timestamp
    le.event = "ta_progress"
    le.trace_id = trace_id
    le.agent_name = "tradingagents"
    return le


def _fake_run(status: str):
    r = MagicMock()
    r.status = status
    r.trace_id = "man-tradingagents-601127-1234"
    r.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    return r


def _setup_db(latest_log, run=None):
    """Build mocks for a running AgentRun -> latest log -> the trace's final record."""
    db = MagicMock()
    active_query = MagicMock()
    active_query.filter.return_value.order_by.return_value.first.return_value = None
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.first.return_value = latest_log
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [active_query, log_query, run_query]
    return db


def test_no_running_task_returns_none():
    """No logs = not running -> a new trigger is allowed."""
    db = _setup_db(latest_log=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_recent_log_no_run_returns_trace():
    """Logs within the last minute and no AgentRun -> running; returns trace_id."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_stale_log_returns_none():
    """No new progress for 5 minutes -> stale -> a new trigger is allowed (returns None)."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_success_returns_none():
    """AgentRun.status=success -> not running (a new trigger is allowed, e.g. re-analysis)."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("success")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_failed_returns_none():
    """AgentRun.status=failed -> not running (re-analysis allowed)."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("failed")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_running_status_returns_trace():
    """AgentRun.status=running with fresh logs -> running."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_running_status_but_stale_returns_none():
    """AgentRun.status=running but logs older than 5 minutes -> stale, not running."""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    # Note: the current implementation returns early only for run.status in ('success','failed');
    # 'running' but stale should be treated as stale -> None
    assert find_active_tradingagents_trace(db, "601127") is None
