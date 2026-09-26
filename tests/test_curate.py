"""Today's must-reads, curated by AI (Phase C)."""
from __future__ import annotations
import asyncio
from src.modules.portfolio.api import dashboard
from src.platform.persistence.database import SessionLocal


class _FakeAI:
    def __init__(self, r): self._r = r
    async def chat(self, s, u, temperature=0.3): return self._r


def test_curate_orders_by_importance(monkeypatch):
    """The importance the AI returns sorts descending, with a why."""
    monkeypatch.setattr(dashboard, "get_configured_failover_client", lambda db, mid=None: _FakeAI("0|40|small move\n1|90|alert triggered"))
    req = dashboard.CurateRequest(candidates=[
        dashboard.CurateCandidate(type="watch", symbol="A", name="Alpha", signal="x"),
        dashboard.CurateCandidate(type="alert", symbol="B", name="Beta", signal="y"),
    ])
    db = SessionLocal()
    try:
        res = asyncio.run(dashboard.curate_today(req, db))
    finally:
        db.close()
    assert res["items"][0]["index"] == 1
    assert res["items"][0]["importance"] == 90
    assert res["items"][0]["why"] == "alert triggered"


def test_curate_fallback_on_ai_fail(monkeypatch):
    """When the AI fails, the original order is kept without an error."""
    class _Boom:
        async def chat(self, *a, **k): raise RuntimeError("boom")
    monkeypatch.setattr(dashboard, "get_configured_failover_client", lambda db, mid=None: _Boom())
    req = dashboard.CurateRequest(candidates=[dashboard.CurateCandidate(type="alert", symbol="B", name="Beta", signal="y")])
    db = SessionLocal()
    try:
        res = asyncio.run(dashboard.curate_today(req, db))
    finally:
        db.close()
    assert len(res["items"]) == 1
    assert res["items"][0]["index"] == 0
