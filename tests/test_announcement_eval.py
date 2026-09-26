"""Announcement positive/negative analysis (Phase B)."""

from __future__ import annotations

import asyncio

from src.modules.research.api import insights
from src.platform.persistence.database import SessionLocal


class _FakeAIClient:
    def __init__(self, reply):
        self._reply = reply

    async def chat(self, system_prompt, user_content, temperature=0.2):
        return self._reply


def test_parse_tone():
    """Positive/negative/neutral parsing (substring-safe)."""
    assert insights._parse_tone("positive, results beat estimates") == "Positive"
    assert insights._parse_tone("Mostly negative") == "Negative"
    assert insights._parse_tone("neutral impact") == "Neutral"
    assert insights._parse_tone("unclear") == "Neutral"


def test_announcement_eval_maps_tone_per_item(monkeypatch):
    """Each announcement maps to the AI's positive/negative verdict."""
    insights._ANN_CACHE.clear()

    async def fake_fetch(symbol, name, limit=5):
        return [
            {"title": "Wins a major order", "time": "2026-06-18 09:00", "content": ""},
            {"title": "Promoter plans stake sale", "time": "2026-06-17 16:00", "content": ""},
        ]

    monkeypatch.setattr(insights, "_fetch_recent_announcements", fake_fetch)
    monkeypatch.setattr(
        insights,
            "get_configured_failover_client",
        lambda db, mid=None: _FakeAIClient("1|positive|order win supports earnings\n2|negative|stake sale weighs"),
    )

    req = insights.AnnouncementEvalRequest(symbol="600519", market="IN")
    db = SessionLocal()
    try:
        res = asyncio.run(insights.announcement_eval(req, db))
    finally:
        db.close()

    assert len(res["items"]) == 2
    assert res["items"][0]["tone"] == "Positive"
    assert res["items"][1]["tone"] == "Negative"


def test_announcement_eval_empty(monkeypatch):
    """No announcements returns an empty list without calling the AI."""
    insights._ANN_CACHE.clear()

    async def fake_fetch(symbol, name, limit=5):
        return []

    called = {"ai": 0}

    def fake_ai(db, mid=None):
        called["ai"] += 1
        return _FakeAIClient("")

    monkeypatch.setattr(insights, "_fetch_recent_announcements", fake_fetch)
    monkeypatch.setattr(insights, "get_configured_failover_client", fake_ai)

    req = insights.AnnouncementEvalRequest(symbol="000001", market="IN")
    db = SessionLocal()
    try:
        res = asyncio.run(insights.announcement_eval(req, db))
    finally:
        db.close()
    assert res["items"] == []
    assert called["ai"] == 0
