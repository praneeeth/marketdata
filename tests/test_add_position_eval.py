"""Quick check before adding: server-side averaged cost + an AI verdict of Suitable / Cautious / Not suitable."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from src.modules.research.api import insights
from src.platform.persistence.database import SessionLocal


class _FakeAIClient:
    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, system_prompt, user_content, temperature=0.3):
        return self._reply


def _run_eval(monkeypatch, req, reply="Verdict: Suitable\nReasons:\n- clear averaging down\nRisk: the market weakens"):
    monkeypatch.setattr(insights, "get_configured_failover_client", lambda db, mid=None: _FakeAIClient(reply))

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(insights, "fetch_realtime_context", _empty)
    monkeypatch.setattr(insights, "_fetch_fundamental_context", _empty)
    monkeypatch.setattr(insights, "fetch_technical_context", _empty)
    monkeypatch.setattr(insights, "_fetch_message_context", _empty)
    db = SessionLocal()
    try:
        return asyncio.run(insights.add_position_eval(req, db))
    finally:
        db.close()


def test_add_position_eval_computes_diluted_cost(monkeypatch):
    """Adding 100@10 to a 100@8 position: cost after averaging 9.0 (down 10%), with the AI verdict."""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="IN",
        current_quantity=100, current_cost=10,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, req)
    assert res["new_cost"] == 9.0
    assert round(res["dilute_pct"], 1) == 10.0
    assert res["total_quantity"] == 200
    assert res["action"] == "Add"
    assert res["verdict"] == "Suitable"


def test_build_position_when_empty(monkeypatch):
    """With no position it opens one: cost = add price, averaging 0."""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="IN",
        current_quantity=0, current_cost=0,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, req)
    assert res["new_cost"] == 8.0
    assert res["dilute_pct"] == 0
    assert res["action"] == "Open position"


def test_verdict_parse_not_confused_by_substring():
    """'Not suitable' contains 'suitable', so parsing must check the longer form first and not misread it as 'Suitable'."""
    assert insights._parse_verdict("Verdict: Not suitable\nReasons: ...") == "Not suitable"
    assert insights._parse_verdict("Verdict: Cautious") == "Cautious"
    assert insights._parse_verdict("Verdict: Suitable") == "Suitable"
    assert insights._parse_verdict("can't tell") == "Unknown"


def test_zero_add_quantity_rejected_by_schema():
    """The quantity to add must be > 0 (rejected at the schema layer)."""
    with pytest.raises(ValidationError):
        insights.AddPositionEvalRequest(
            symbol="600519", add_quantity=0, add_price=8,
        )
