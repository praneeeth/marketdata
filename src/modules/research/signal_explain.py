"""Signal explainability (Phase 3): rank_score -> a 1-10 AI score + positive/negative factor breakdown.

Modelled on Danelfin's 1-10 AI Score and green/red AI Factors: splits the score_breakdown strategy_engine already computed
(alpha/catalyst/quality/source_bonus add points; risk/crowd penalties subtract them)
into "positive (green, lifts) / negative (red, drags)" groups for the opportunities page.

Pure functions with no DB; injected by the API layer as post-processing of list_strategy_signals.
"""

from __future__ import annotations

# Factor display labels
FACTOR_LABELS = {
    "alpha_score": "Stock alpha",
    "catalyst_score": "Catalyst",
    "quality_score": "Plan quality",
    "source_bonus": "Source bonus",
    "risk_penalty": "Risk",
    "crowd_penalty": "Crowding",
}

# Additive factors (positive = lifts, negative = drags)
ADDITIVE_FACTORS = ("alpha_score", "catalyst_score", "quality_score", "source_bonus")
# Penalty factors (positive = drags; score_breakdown stores penalty strength as a positive number)
PENALTY_FACTORS = ("risk_penalty", "crowd_penalty")

_EPS = 0.01


def to_ai_score(rank_score) -> int:
    """rank_score (0-100) -> 1-10 AI score (clamped to [1, 10])."""
    try:
        s = float(rank_score or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    return max(1, min(10, round(s / 10.0)))


def explain_factors(score_breakdown) -> dict:
    """Split into positive (green) / negative (red) groups, each sorted by absolute contribution, top 5."""
    sb = score_breakdown if isinstance(score_breakdown, dict) else {}
    positive: list[dict] = []
    negative: list[dict] = []

    def _f(key):
        try:
            return float(sb.get(key))
        except (TypeError, ValueError):
            return None

    for key in ADDITIVE_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:
            positive.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})
        elif v < -_EPS:
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})

    for key in PENALTY_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:  # a positive penalty drags, so its contribution is negative
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(-v, 2)})

    positive.sort(key=lambda x: x["contribution"], reverse=True)
    negative.sort(key=lambda x: x["contribution"])  # most negative first
    return {"positive": positive[:5], "negative": negative[:5]}


def enrich_signal(item: dict) -> dict:
    """Add ai_score + factor_explain to a signal item (modifies it in place and returns it)."""
    if not isinstance(item, dict):
        return item
    item["ai_score"] = to_ai_score(item.get("rank_score"))
    item["factor_explain"] = explain_factors(item.get("score_breakdown"))
    return item
