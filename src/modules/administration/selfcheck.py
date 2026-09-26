"""System self-check (Doctor): checks data sources / AI / notifications in one go, with fix hints.

Reuses each area's existing test logic (broker status, AI AIClient.chat, notifications NotifierManager)
instead of new probes, and adds two things: (1) concurrent aggregation into one dashboard (2) common errors -> actionable fix hints.

Notifications are **only validated (URI config), not sent** by default (no spam); notify_send=True really sends.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.platform.persistence.database import SessionLocal

logger = logging.getLogger(__name__)

SLOW_MS = 4000          # above this counts as "slow"
PROBE_TIMEOUT_S = 20    # timeout per probe


def classify_hint(category: str, error: str | None) -> str:
    """Error -> actionable fix hint. Covers the most common self-hosting proxy/auth/config problems."""
    e = (error or "").lower()
    if category == "datasource":
        if "expired" in e or "not logged in" in e:
            return "Broker session expired: log in again under Data sources (Kite and Upstox sessions end daily)."
        if "could not be read" in e or "credentials_master_key" in e:
            return "Stored broker keys can't be decrypted: check CREDENTIALS_MASTER_KEY, or enter the keys again."
        return "Broker not connected: open Data sources, save your API keys and log in."
    if category == "ai":
        if any(k in e for k in ("401", "unauthorized", "invalid_api_key", "api key", "incorrect api key", "authentication")):
            return "AI authentication failed: the API key is wrong or expired; check the provider's api_key."
        if any(k in e for k in ("model", "not found", "does not exist", "404")):
            return "Model not found: check the model name matches the provider's."
        if any(k in e for k in ("429", "rate limit", "quota", "insufficient", "balance")):
            return "Rate limited or out of quota: try again later, or check the account balance/quota."
        if any(k in e for k in ("connect", "timeout", "timed out", "proxy", "ssl", "getaddrinfo", "name resolution")):
            return "Can't reach the AI service: check base_url, and whether a proxy is needed or wrongly set."
            return "AI call failed: check the base_url / api_key / model config one by one."
    if category == "notify":
        if any(k in e for k in ("invalid", "unsupported", "scheme", "malformed", "parse", "config")):
            return "Invalid notification config: check the channel URL/parameter format (Apprise URI)."
        if any(k in e for k in ("forbidden", "unauthorized", "403", "401", "404", "blocked", "connect", "timeout")):
            return "Notification failed: check the webhook address/token, and whether the network blocks it."
            return "Notifications not getting through: check the channel config, or click Test on the channel page to send for real."
    if category == "system":
        if "lock" in e:
            return "SQLite is locked: concurrent schedules plus a slow proxy; reduce concurrency or speed up/turn off the proxy."
        if any(k in e for k in ("disk", "space")):
            return "Low disk space: clean old data/logs out of the data directory, or add disk space."
        if any(k in e for k in ("scheduler", "stopped", "not running")):
            return "Scheduler not running/stopped: restart the service to resume scheduled tasks."
        return error or "System check failed; see the logs."
    return error or "Unknown error; see the logs."


def _item(category: str, key: str, name: str, status: str,
          latency_ms: int, error: str | None = None, note: str | None = None) -> dict:
    return {
        "category": category,
        "key": key,
        "name": name,
        "status": status,  # ok | slow | fail
        "latency_ms": int(latency_ms),
        "error": error,
        "hint": classify_hint(category, error) if status == "fail" else "",
        "note": note,
    }


def _status_for(success: bool, latency_ms: int) -> str:
    if not success:
        return "fail"
    return "slow" if latency_ms > SLOW_MS else "ok"


async def probe_broker(connection: dict) -> dict:
    """A broker connection's stored status. No network call, so no broker rate limit is used."""
    status = connection.get("status")
    key = f"broker:{connection.get('provider')}"
    name = connection.get("label") or connection.get("provider") or "broker"
    if status == "connected":
        return _item("datasource", key, name, "ok", 0)
    error = {
        "expired": "session expired",
        "error": connection.get("last_error") or "login failed",
    }.get(str(status), "not logged in")
    return _item("datasource", key, name, "fail", 0, error)


async def probe_ai_model(model, service) -> dict:
    """Send a very short ping through AIClient.chat."""
    from src.platform.ai.ai_client import AIClient

    name = model.name or model.model
    t0 = time.monotonic()
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key, model=model.model)
        await client.chat(system_prompt="You are a helpful assistant.",
                          user_content="Say 'OK'.", temperature=0)
        latency = int((time.monotonic() - t0) * 1000)
        return _item("ai", f"ai:{model.id}", name, _status_for(True, latency), latency)
    except Exception as e:
        return _item("ai", f"ai:{model.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_notify_channel(channel, *, send: bool = False) -> dict:
    """By default only validate the URI config (add_channel raises if invalid); send=True really sends."""
    from src.platform.notifications.notifier import NotifierManager

    name = channel.name or channel.type
    t0 = time.monotonic()
    try:
        notifier = NotifierManager()
        notifier.add_channel(channel.type, channel.config or {})  # raises on an invalid URI
        if not send:
            latency = int((time.monotonic() - t0) * 1000)
            return _item("notify", f"nc:{channel.id}", name, "ok", latency,
                         note="Config format validated only, nothing sent (tick \"Send for real\" to send a test message)")
        result = await notifier.notify_with_result(
            title="System self-check", content="Self-check test message from the app.", bypass_quiet_hours=True)
        latency = int((time.monotonic() - t0) * 1000)
        ok = bool(result.get("success"))
        return _item("notify", f"nc:{channel.id}", name, _status_for(ok, latency), latency,
                     None if ok else (result.get("error") or "Send failed"))
    except Exception as e:
        return _item("notify", f"nc:{channel.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_db() -> dict:
    """Run SELECT 1 against the real database."""
    from sqlalchemy import text

    from src.platform.persistence.database import SessionLocal

    t0 = time.monotonic()
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        latency = int((time.monotonic() - t0) * 1000)
        return _item("system", "sys:db", "Database", _status_for(True, latency), latency)
    except Exception as e:
        return _item("system", "sys:db", "Database", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_disk() -> dict:
    """Check free space on the disk holding the data directory."""
    import os
    import shutil

    from src.platform.persistence.database import DB_PATH

    t0 = time.monotonic()
    try:
        data_dir = os.path.dirname(os.path.abspath(DB_PATH))
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        note = f"{free_gb:.1f}GB free of {total_gb:.1f}GB"
        latency = int((time.monotonic() - t0) * 1000)
        if free_gb < 0.2:
            return _item("system", "sys:disk", "Disk space", "fail", latency,
                         error=f"Disk space critically low ({note})", note=note)
        status = "slow" if free_gb < 1.0 else "ok"
        return _item("system", "sys:disk", "Disk space", status, latency, note=note)
    except Exception as e:
        return _item("system", "sys:disk", "Disk space", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_scheduler() -> dict:
    """Look at running schedulers through scheduler_registry; an empty registry (CLI/not started) is skipped gracefully."""
    from src.platform.scheduling import scheduler_registry

    regs = scheduler_registry.get_all()
    if not regs:
        return _item("system", "sys:scheduler", "Scheduler", "ok", 0,
                     note="No scheduler running in this process (the CLI self-check skips this item)")
    running: list[str] = []
    stopped: list[str] = []
    jobs = 0
    for name, sched in regs.items():
        try:
            if getattr(sched, "running", False):
                running.append(name)
                jobs += len(sched.get_jobs())
            else:
                stopped.append(name)
        except Exception:
            stopped.append(name)
    if running:
        note = f"{len(running)} schedulers running, {jobs} jobs in total"
        if stopped:
            note += f"; stopped: {', '.join(stopped)}"
        return _item("system", "sys:scheduler", "Scheduler", "ok", 0, note=note)
    return _item("system", "sys:scheduler", "Scheduler", "fail", 0,
                 error=f"Schedulers stopped: {', '.join(stopped)}")


async def _guard(coro, fallback: dict) -> dict:
    """Wrap each probe in a timeout; probes already try/except, so this only catches timeouts/unexpected errors."""
    try:
        return await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", PROBE_TIMEOUT_S * 1000, f"Probe timed out (>{PROBE_TIMEOUT_S}s)")
    except Exception as e:  # pragma: no cover - defensive
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", 0, str(e))


def _enumerate(db, include_system: bool = True) -> list[dict]:
    """List every item to check (identity + ORM reference) without probing. include_system adds the DB/disk/scheduler items."""
    from src.modules.market.brokers import get_broker_manager
    from src.platform.persistence.models import AIModel, AIService, NotifyChannel

    targets: list[dict] = []
    if include_system:
        targets.append({"category": "system", "key": "sys:db", "name": "Database", "group": None, "_kind": "db"})
        targets.append({"category": "system", "key": "sys:disk", "name": "Disk space", "group": None, "_kind": "disk"})
        targets.append({"category": "system", "key": "sys:scheduler", "name": "Scheduler", "group": None, "_kind": "sched"})
    for conn in get_broker_manager().connections(db):
        if conn.get("enabled"):
            targets.append({"category": "datasource", "key": f"broker:{conn['provider']}",
                            "name": conn["label"], "group": None, "_kind": "broker", "_obj": conn})
    for model in db.query(AIModel).all():
        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if not service:
            continue
        # group = provider name, so the frontend can show a provider -> model hierarchy
        targets.append({"category": "ai", "key": f"ai:{model.id}", "name": model.name or model.model,
                        "group": service.name, "_kind": "ai", "_obj": model, "_service": service})
    for ch in db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all():
        targets.append({"category": "notify", "key": f"nc:{ch.id}", "name": ch.name or ch.type,
                        "group": None, "_kind": "nc", "_obj": ch})
    return targets


def _identity(t: dict) -> dict:
    return {"category": t["category"], "key": t["key"], "name": t["name"], "group": t.get("group")}


def _probe_for(t: dict, notify_send: bool):
    kind = t["_kind"]
    if kind == "db":
        return probe_db()
    if kind == "disk":
        return probe_disk()
    if kind == "sched":
        return probe_scheduler()
    if kind == "broker":
        return probe_broker(t["_obj"])
    if kind == "ai":
        return probe_ai_model(t["_obj"], t["_service"])
    return probe_notify_channel(t["_obj"], send=notify_send)


def list_selfcheck_items(*, db=None, include_system: bool = True) -> list[dict]:
    """List only the identities of items to check (category/key/name/group), without probing; the frontend renders the list first, then checks each."""
    own = db is None
    db = db or SessionLocal()
    try:
        return [_identity(t) for t in _enumerate(db, include_system)]
    finally:
        if own:
            db.close()


async def run_selfcheck(*, db=None, notify_send: bool = False, keys=None, include_system: bool = True) -> dict:
    """Probe the items and return the dashboard. With keys, only those keys are probed (so the frontend can update progress item by item)."""
    own = db is None
    db = db or SessionLocal()
    try:
        keyset = set(keys) if keys is not None else None
        targets = [t for t in _enumerate(db, include_system) if keyset is None or t["key"] in keyset]
        tasks = [_guard(_probe_for(t, notify_send), _identity(t)) for t in targets]
        items = list(await asyncio.gather(*tasks)) if tasks else []
        summary = {
            "total": len(items),
            "ok": sum(1 for i in items if i["status"] == "ok"),
            "slow": sum(1 for i in items if i["status"] == "slow"),
            "fail": sum(1 for i in items if i["status"] == "fail"),
        }
        return {"items": items, "summary": summary, "notify_send": bool(notify_send)}
    finally:
        if own:
            db.close()
