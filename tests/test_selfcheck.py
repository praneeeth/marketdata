"""Self-check: classify_hint repair hints, broker probes and run_selfcheck aggregation."""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --------------------------- classify_hint (pure function) ---------------------------

def test_hint_broker_session_expired():
    """Expired broker session -> tell the user to log in again."""
    from src.modules.administration.selfcheck import classify_hint

    assert "log in again" in classify_hint("datasource", "session expired")
    assert "log in again" in classify_hint("datasource", "not logged in")


def test_hint_broker_keys_unreadable():
    from src.modules.administration.selfcheck import classify_hint

    assert "CREDENTIALS_MASTER_KEY" in classify_hint("datasource", "Stored credentials could not be read.")
    assert "Data sources" in classify_hint("datasource", "login failed")


def test_probe_broker_reads_stored_status():
    """No network call: the stored connection status decides the result."""
    from src.modules.administration.selfcheck import probe_broker

    ok = asyncio.run(probe_broker({"provider": "kite", "label": "Kite", "status": "connected"}))
    assert (ok["status"], ok["key"], ok["error"]) == ("ok", "broker:kite", None)
    exp = asyncio.run(probe_broker({"provider": "kite", "label": "Kite", "status": "expired"}))
    assert (exp["status"], exp["error"]) == ("fail", "session expired")
    err = asyncio.run(probe_broker({"provider": "angel", "status": "error", "last_error": "bad totp"}))
    assert err["error"] == "bad totp"
    assert err["name"] == "angel"
    off = asyncio.run(probe_broker({"provider": "upstox", "label": "Upstox", "status": "disconnected"}))
    assert off["error"] == "not logged in"


class _FakeBrokers:
    def __init__(self, conns):
        self._conns = conns

    def connections(self, db):
        return self._conns


def _patch_brokers(monkeypatch, conns):
    import src.modules.market.brokers as brokers

    monkeypatch.setattr(brokers, "get_broker_manager", lambda: _FakeBrokers(conns))


KITE = {"provider": "kite", "label": "Zerodha Kite Connect", "enabled": True, "status": "connected"}
DISABLED = {"provider": "angel", "label": "Angel One", "enabled": False, "status": "connected"}


def test_hint_ai_auth():
    """AI 401 -> hint about the API key / auth."""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("ai", "Error code: 401 - invalid_api_key")
    assert "key" in h.lower() or "auth" in h.lower()


def test_hint_ai_model_not_found():
    """AI model not found -> hint about the model name."""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("ai", "The model `gpt-x` does not exist (404)")
    assert "model" in h.lower()


def test_hint_notify_invalid():
    """Invalid notification URI -> hint about config / format."""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("notify", "Unsupported URL or invalid scheme")
    assert "config" in h or "URI" in h or "format" in h


def test_hint_system_disk():
    """Disk errors -> hint about space / cleanup."""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("system", "disk space low: only 50MB free")
    assert "disk" in h.lower() or "space" in h.lower()


def test_hint_system_scheduler():
    """Scheduler stopped -> hint to restart."""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("system", "scheduler stopped")
    assert "scheduler" in h.lower()


# --------------------------- System items (DB/disk/scheduler) ---------------------------

def test_probe_db_ok():
    """The DB probe runs SELECT 1 against the real database and should pass."""
    from src.modules.administration.selfcheck import probe_db

    r = asyncio.run(probe_db())
    assert r["category"] == "system" and r["key"] == "sys:db"
    assert r["status"] in ("ok", "slow")


def test_probe_disk_ok():
    """The disk probe returns usage; a normal machine should pass with a note."""
    from src.modules.administration.selfcheck import probe_disk

    r = asyncio.run(probe_disk())
    assert r["key"] == "sys:disk"
    assert r["status"] in ("ok", "slow", "fail")
    assert r["note"]  # shows free/total


def test_probe_scheduler_empty_registry_ok():
    """No registered scheduler (e.g. CLI/not started) -> ok with a note, not a false failure."""
    from src.platform.scheduling import scheduler_registry
    from src.modules.administration.selfcheck import probe_scheduler

    scheduler_registry.clear()
    r = asyncio.run(probe_scheduler())
    assert r["key"] == "sys:scheduler" and r["status"] == "ok"
    assert r["note"]


def test_probe_scheduler_running():
    """A running scheduler is registered -> ok, with the job count in the note."""
    from src.platform.scheduling import scheduler_registry
    from src.modules.administration.selfcheck import probe_scheduler

    class _FakeSched:
        running = True

        def get_jobs(self):
            return [1, 2, 3]

    scheduler_registry.clear()
    scheduler_registry.register("agent", _FakeSched())
    try:
        r = asyncio.run(probe_scheduler())
        assert r["status"] == "ok"
        assert "3" in r["note"]
    finally:
        scheduler_registry.clear()


def test_run_selfcheck_always_includes_system_items():
    """An empty database still has the 3 system items (database/disk/scheduler)."""
    from src.modules.administration.selfcheck import run_selfcheck

    db = _mem_db()
    try:
        from src.platform.scheduling import scheduler_registry
        scheduler_registry.clear()
        res = asyncio.run(run_selfcheck(db=db))
        keys = {i["key"] for i in res["items"]}
        assert {"sys:db", "sys:disk", "sys:scheduler"} <= keys
    finally:
        db.close()


# --------------------------- run_selfcheck (aggregation) ---------------------------

def test_run_selfcheck_aggregates(monkeypatch):
    """List enabled items -> probe concurrently -> aggregate the summary (total/ok/slow/fail)."""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import AIModel, AIService, NotifyChannel

    _patch_brokers(monkeypatch, [KITE, DISABLED])
    db = _mem_db()
    try:
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        svc = AIService(name="deepseek", base_url="https://x", api_key="k")
        db.add(svc)
        db.flush()
        db.add(AIModel(name="ds-chat", model="deepseek-chat", service_id=svc.id))
        db.commit()

        async def fake_ds(conn):
            return {"category": "datasource", "key": f"broker:{conn['provider']}", "name": conn["label"],
                    "status": "ok", "latency_ms": 10, "error": None, "hint": ""}

        async def fake_ai(model, service):
            return {"category": "ai", "key": f"ai:{model.id}", "name": model.name,
                    "status": "fail", "latency_ms": 20, "error": "401", "hint": "wrong key"}

        async def fake_nc(channel, send=False):
            return {"category": "notify", "key": f"nc:{channel.id}", "name": channel.name,
                    "status": "ok", "latency_ms": 5, "error": None, "hint": ""}

        monkeypatch.setattr(selfcheck, "probe_broker", fake_ds)
        monkeypatch.setattr(selfcheck, "probe_ai_model", fake_ai)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", fake_nc)

        res = asyncio.run(selfcheck.run_selfcheck(db=db, include_system=False))
        assert res["summary"] == {"total": 3, "ok": 2, "slow": 0, "fail": 1}
        assert {i["category"] for i in res["items"]} == {"datasource", "ai", "notify"}
    finally:
        db.close()


def test_run_selfcheck_empty_db():
    """No enabled items -> an empty dashboard without errors."""
    from src.modules.administration.selfcheck import run_selfcheck

    db = _mem_db()
    try:
        res = asyncio.run(run_selfcheck(db=db, include_system=False))
        assert res["summary"]["total"] == 0
        assert res["items"] == []
    finally:
        db.close()


def test_list_selfcheck_items_no_probe(monkeypatch):
    """List mode only enumerates the identities to check (category/key/name), without probing."""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import NotifyChannel

    _patch_brokers(monkeypatch, [KITE, DISABLED])
    db = _mem_db()
    try:
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        db.commit()

        called = {"n": 0}

        async def boom(*a, **k):
            called["n"] += 1
            return {}

        monkeypatch.setattr(selfcheck, "probe_broker", boom)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", boom)

        items = selfcheck.list_selfcheck_items(db=db, include_system=False)
        assert {i["key"] for i in items} == {"broker:kite", "nc:1"}  # disabled Angel is skipped
        assert all({"category", "key", "name", "group"} <= set(i) for i in items)
        assert called["n"] == 0  # no probe was triggered
    finally:
        db.close()


def test_list_items_ai_has_service_group():
    """AI items carry group = provider name (for the frontend's provider -> model hierarchy)."""
    from src.modules.administration.selfcheck import list_selfcheck_items
    from src.platform.persistence.models import AIModel, AIService

    db = _mem_db()
    try:
        svc = AIService(name="DeepSeek", base_url="https://x", api_key="k")
        db.add(svc)
        db.flush()
        db.add(AIModel(name="ds-chat", model="deepseek-chat", service_id=svc.id))
        db.add(AIModel(name="ds-reasoner", model="deepseek-reasoner", service_id=svc.id))
        db.commit()
        items = list_selfcheck_items(db=db)
        ai = [i for i in items if i["category"] == "ai"]
        assert len(ai) == 2
        assert all(i["group"] == "DeepSeek" for i in ai)
    finally:
        db.close()


def test_run_selfcheck_keys_filter(monkeypatch):
    """keys filter: only probe the given keys (so the frontend can update progress item by item)."""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import NotifyChannel

    _patch_brokers(monkeypatch, [KITE, DISABLED])
    db = _mem_db()
    try:
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        db.commit()

        async def fake_ds(conn):
            return {"category": "datasource", "key": f"broker:{conn['provider']}", "name": conn["label"],
                    "status": "ok", "latency_ms": 1, "error": None, "hint": ""}

        async def fake_nc(c, send=False):
            return {"category": "notify", "key": f"nc:{c.id}", "name": c.name,
                    "status": "ok", "latency_ms": 1, "error": None, "hint": ""}

        monkeypatch.setattr(selfcheck, "probe_broker", fake_ds)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", fake_nc)

        res = asyncio.run(selfcheck.run_selfcheck(db=db, keys=["broker:kite"]))
        assert res["summary"]["total"] == 1
        assert res["items"][0]["key"] == "broker:kite"
    finally:
        db.close()


# --------------------------- Endpoints ---------------------------

def test_selfcheck_endpoint(monkeypatch):
    """The endpoint calls run_selfcheck and returns the dashboard unchanged."""
    from src.modules.administration.api import health

    async def fake_run(*, notify_send=False, keys=None):
        return {"items": [], "summary": {"total": 0, "ok": 0, "slow": 0, "fail": 0},
                "notify_send": notify_send}

    monkeypatch.setattr(health, "run_selfcheck", fake_run)
    # Calling the route function directly needs explicit arguments (Query defaults only resolve in HTTP requests)
    res = asyncio.run(health.selfcheck(notify_send=True, list_only=False, keys=None))
    assert res["summary"]["total"] == 0
    assert res["notify_send"] is True


def test_selfcheck_route_mounted():
    """/api/health/selfcheck is mounted on the app."""
    from src.bootstrap.application import app

    assert "/api/health/selfcheck" in set(app.openapi().get("paths", {}).keys())


# --------------------------- CLI doctor ---------------------------

def test_doctor_print_report(capsys):
    """The make doctor report: grouped output, with the error and fix hint for failed items."""
    from src.modules.administration.doctor import _print_report

    res = {
        "summary": {"total": 2, "ok": 1, "slow": 0, "fail": 1},
        "items": [
            {"category": "system", "key": "sys:db", "name": "Database", "group": None,
             "status": "ok", "latency_ms": 5, "error": None, "hint": "", "note": None},
            {"category": "datasource", "key": "broker:kite", "name": "Zerodha Kite Connect", "group": None,
             "status": "fail", "latency_ms": 0, "error": "timeout", "hint": "check the proxy settings", "note": None},
        ],
    }
    _print_report(res)
    out = capsys.readouterr().out
    assert "system self-check" in out
    assert "[System]" in out and "[Data sources]" in out
    assert "Database" in out and "Zerodha Kite Connect" in out
    assert "check the proxy settings" in out and "❌" in out


# Removed: scheduled alerts for self-check results (selfcheck_and_notify); the user asked for no result notifications. The dialog's "send for real" switch stays.
