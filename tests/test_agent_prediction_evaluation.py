"""Trading-day and grouping semantics of agent item outcome evaluation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  registers the ORM models
from src.platform.persistence.database import Base


def _bar(day: str, close: float):
    return SimpleNamespace(date=day, close=close)


def test_friday_prediction_one_trading_day_uses_monday_close():
    """For an item on a Friday, the 1-trading-day result uses Monday's close, not Saturday."""
    from src.modules.research.prediction_outcome import _find_close_after_n_trading_days

    bars = [_bar("2026-08-28", 10), _bar("2026-08-31", 11)]

    assert _find_close_after_n_trading_days(bars, date(2026, 8, 28), 1) == 11


def test_two_horizons_saved_for_one_suggestion_share_group_id(monkeypatch, recommendations_enabled):
    """The 1- and 5-trading-day rows of one item must share a group ID."""
    from src.modules.research.context_store import save_agent_prediction_outcome
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import AgentPredictionOutcome

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("src.modules.research.context_store.SessionLocal", lambda: session)

    try:
        for horizon in (1, 5):
            assert save_agent_prediction_outcome(
                agent_name="daily_report",
                stock_symbol="600000",
                stock_market="CN",
                prediction_date="2026-08-28",
                horizon_days=horizon,
                action="buy",
                action_label="Buy",
                prediction_group_id="group-1",
            )

        rows = session.query(AgentPredictionOutcome).order_by(AgentPredictionOutcome.horizon_days).all()
        assert [row.prediction_group_id for row in rows] == ["group-1", "group-1"]
        assert [row.horizon_unit for row in rows] == ["trading_days", "trading_days"]
    finally:
        session.close()



def test_research_only_does_not_record_predictions(monkeypatch):
    """Research-only mode never stores buy/sell/hold predictions."""
    from src.modules.research.context_store import save_agent_prediction_outcome

    def _fail():
        raise AssertionError("database must not be touched in research-only mode")

    monkeypatch.setattr("src.modules.research.context_store.SessionLocal", _fail)
    assert (
        save_agent_prediction_outcome(
            agent_name="daily_report",
            stock_symbol="600000",
            stock_market="CN",
            prediction_date="2026-08-28",
            horizon_days=1,
            action="buy",
            action_label="Buy",
        )
        is False
    )


def _outcome(
    group_id: str,
    horizon: int,
    return_pct: float | None,
    *,
    action: str = "buy",
    status: str = "evaluated",
    unit: str = "trading_days",
):
    return SimpleNamespace(
        id=horizon,
        prediction_group_id=group_id,
        agent_name="daily_report",
        stock_symbol="600000",
        stock_market="CN",
        prediction_date="2026-08-28",
        horizon_days=horizon,
        horizon_unit=unit,
        action=action,
        action_label="Buy",
        confidence=0.8,
        trigger_price=10.0,
        outcome_price=10.0 if return_pct is None else 10.0 * (1 + return_pct / 100),
        outcome_return_pct=return_pct,
        outcome_status=status,
        meta={"reason": "test reason", "signal": "test signal"},
        evaluated_at=None,
        created_at=None,
    )


def test_classify_prediction_hit_matches_declared_policy():
    """Buy/sell direction and the watch/flat threshold are decided by the backend."""
    from src.modules.automation.agent_prediction_evaluation import classify_prediction_hit

    assert classify_prediction_hit("add", 0.01) is True
    assert classify_prediction_hit("reduce", -0.01) is True
    assert classify_prediction_hit("watch", 1.99) is True
    assert classify_prediction_hit("watch", 2.0) is False
    assert classify_prediction_hit("unknown", 1.0) is None


def test_group_prediction_outcomes_pivots_one_and_five_days():
    """The 1- and 5-trading-day results of one group take one row in the frontend."""
    from src.modules.automation.agent_prediction_evaluation import group_prediction_outcomes

    groups = group_prediction_outcomes(
        [_outcome("group-1", 1, 1.2), _outcome("group-1", 5, -2.0)]
    )

    assert len(groups) == 1
    assert groups[0]["prediction_group_id"] == "group-1"
    assert groups[0]["outcomes"]["1"]["hit"] is True
    assert groups[0]["outcomes"]["5"]["hit"] is False


def test_legacy_same_day_suggestions_are_not_merged():
    """Two items on the same day and direction in old data must still be two review rows."""
    from src.modules.automation.agent_prediction_evaluation import group_prediction_outcomes

    first_created_at = datetime(2026, 8, 28, 9, 0, 0)
    second_created_at = first_created_at + timedelta(minutes=30)
    def legacy_row(record_id: int, horizon: int, return_pct: float, created_at: datetime):
        payload = vars(_outcome("", horizon, return_pct)).copy()
        payload.update(id=record_id, prediction_group_id=None, created_at=created_at)
        return SimpleNamespace(**payload)

    rows = [
        legacy_row(10, 1, 1.0, first_created_at),
        legacy_row(11, 5, 2.0, first_created_at),
        legacy_row(12, 1, -1.0, second_created_at),
        legacy_row(13, 5, -2.0, second_created_at),
    ]

    groups = group_prediction_outcomes(rows)

    assert len(groups) == 2
    assert {group["outcomes"]["1"]["return_pct"] for group in groups} == {1.0, -1.0}


def test_legacy_horizons_saved_across_seconds_stay_in_one_group():
    """When old writes committed the 1- and 5-day rows separately, they still belong to one item even across a second boundary."""
    from src.modules.automation.agent_prediction_evaluation import group_prediction_outcomes

    first_created_at = datetime(2026, 8, 28, 9, 0, 0)
    one_day = vars(_outcome("", 1, 1.0)).copy()
    one_day.update(prediction_group_id=None, id=100, created_at=first_created_at)
    five_day = vars(_outcome("", 5, 2.0)).copy()
    five_day.update(
        prediction_group_id=None,
        id=101,
        created_at=first_created_at + timedelta(seconds=1),
    )

    groups = group_prediction_outcomes(
        [SimpleNamespace(**one_day), SimpleNamespace(**five_day)]
    )

    assert len(groups) == 1
    assert set(groups[0]["outcomes"]) == {"1", "5"}


def test_interleaved_legacy_writes_are_not_cross_paired():
    """Concurrent writes with the same old group key are better split than wrongly cross-paired."""
    from src.modules.automation.agent_prediction_evaluation import group_prediction_outcomes

    created_at = datetime(2026, 8, 28, 9, 0, 0)
    rows = []
    for record_id, horizon, return_pct in (
        (200, 1, 1.0), (201, 1, -1.0), (202, 5, 5.0), (203, 5, -5.0),
    ):
        payload = vars(_outcome("", horizon, return_pct)).copy()
        payload.update(
            id=record_id,
            prediction_group_id=None,
            created_at=created_at + timedelta(seconds=record_id - 200),
        )
        rows.append(SimpleNamespace(**payload))

    groups = group_prediction_outcomes(rows)

    assert len(groups) == 4
    assert all(len(group["outcomes"]) == 1 for group in groups)


def test_summary_marks_less_than_twenty_completed_samples_insufficient():
    """Fewer than 20 completed samples mustn't be presented as a stable hit rate."""
    from src.modules.automation.agent_prediction_evaluation import (
        group_prediction_outcomes,
        summarize_prediction_groups,
    )

    groups = group_prediction_outcomes(
        [_outcome(f"group-{index}", 5, 1.0) for index in range(19)]
    )

    assert summarize_prediction_groups(groups)["insufficient_sample"] is True
