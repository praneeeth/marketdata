"""Unit tests for signal explainability (Phase 3); pure functions."""

from src.modules.research.signal_explain import enrich_signal, explain_factors, to_ai_score


def test_ai_score_mapping():
    """rank_score 0-100 maps to 1-10, clamped."""
    assert to_ai_score(0) == 1
    assert to_ai_score(50) == 5
    assert to_ai_score(100) == 10
    assert to_ai_score(30) == 3
    assert to_ai_score(None) == 1  # fallback


def test_explain_factors_splits_positive_negative():
    """Positive additive factors go green, negative ones red; penalty factors go red."""
    sb = {"alpha_score": 5.0, "catalyst_score": -2.0, "quality_score": 3.0,
          "risk_penalty": 3.0, "crowd_penalty": 0.0}
    r = explain_factors(sb)
    pos = {x["factor"] for x in r["positive"]}
    neg = {x["factor"] for x in r["negative"]}
    assert "alpha_score" in pos and "quality_score" in pos
    assert "catalyst_score" in neg   # a negative additive factor drags
    assert "risk_penalty" in neg     # a positive penalty drags
    assert "crowd_penalty" not in neg  # 0 isn't counted


def test_explain_factors_penalty_contribution_negative():
    """A penalty factor's contribution is negative (a drag)."""
    r = explain_factors({"risk_penalty": 4.0})
    item = next(x for x in r["negative"] if x["factor"] == "risk_penalty")
    assert item["contribution"] == -4.0


def test_enrich_signal_adds_fields():
    """enrich_signal adds ai_score and factor_explain."""
    out = enrich_signal({"rank_score": 80, "score_breakdown": {"alpha_score": 5.0}})
    assert out["ai_score"] == 8
    assert "positive" in out["factor_explain"]


def test_explain_factors_empty():
    """An empty breakdown returns two empty groups without an error."""
    r = explain_factors(None)
    assert r == {"positive": [], "negative": []}
