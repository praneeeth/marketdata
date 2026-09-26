"""Scoring integration (M3): _compute_factor_breakdown weights each factor + no regression."""

from __future__ import annotations

from src.modules.strategy.strategy_engine import _compute_factor_breakdown
from src.platform.persistence.models import EntryCandidate


def _candidate(**kw):
    """Build an in-memory EntryCandidate (not saved); score=80 -> raw alpha_score=13.5."""
    defaults = dict(
        score=80.0, action="watch", status="active", plan_quality=80,
        candidate_source="watchlist", is_holding_snapshot=True,
        signal="", reason="", source_agent="", entry_low=None, entry_high=None, meta=None,
    )
    defaults.update(kw)
    return EntryCandidate(**defaults)


def _bd(row, fw):
    return _compute_factor_breakdown(
        row=row, strategy_code="pullback", weight=1.0,
        risk_level="low", regime_info=None, factor_weights=fw,
    )


def test_factor_weights_none_equals_empty_zero_regression():
    """factor_weights = None / {} / all 1.0 give identical output (no regression)."""
    row = _candidate()
    a = _bd(row, None)
    b = _bd(row, {})
    c = _bd(row, {
        "alpha_score": 1.0, "catalyst_score": 1.0, "quality_score": 1.0,
        "risk_penalty": 1.0, "crowd_penalty": 1.0,
    })
    assert a["raw_score"] == b["raw_score"] == c["raw_score"]
    assert a["weighted_score"] == b["weighted_score"] == c["weighted_score"]


def test_factor_weight_boost_adds_one_raw_factor():
    """alpha weight 1.0 -> 2.0: raw_score gains exactly one extra raw alpha_score; snapshot values aren't affected by weights."""
    row = _candidate()
    base = _bd(row, {"alpha_score": 1.0})
    boosted = _bd(row, {"alpha_score": 2.0})

    # The snapshot stores raw factor scores (IC is measured on raw), unchanged by weights
    assert boosted["alpha_score"] == base["alpha_score"]
    assert base["alpha_score"] > 0
    # Weighting only shows in the combined raw_score
    assert abs((boosted["raw_score"] - base["raw_score"]) - base["alpha_score"]) < 1e-6


def test_penalty_weight_increases_deduction():
    """A higher penalty factor weight -> more deducted -> lower raw_score."""
    # Create a non-zero risk_penalty: status not active (+2.5)
    row = _candidate(status="inactive")
    base = _bd(row, {"risk_penalty": 1.0})
    heavier = _bd(row, {"risk_penalty": 2.0})
    assert base["risk_penalty"] > 0
    assert heavier["raw_score"] < base["raw_score"]
