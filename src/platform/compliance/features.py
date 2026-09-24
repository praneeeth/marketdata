"""Feature gates for recommendation machinery and other restricted surfaces."""

from __future__ import annotations

import os
from enum import StrEnum

from src.platform.compliance.settings import ComplianceSettings, get_compliance_settings


class Feature(StrEnum):
    # Recommendation machinery (ARCHITECTURE §5.5). Off whenever recommendations are not
    # publishable, which in Phase 1 means always.
    SUGGESTION_POOL = "suggestion_pool"
    PREDICTION_TRACKING = "prediction_tracking"
    ENTRY_CANDIDATES = "entry_candidates"
    STRATEGY_SIGNALS = "strategy_signals"
    EVALUATIONS = "evaluations"
    AI_PAPER_TRADING = "ai_paper_trading"
    POSITION_CALCULATOR = "position_calculator"
    SHARE_CARDS = "share_cards"
    TRADINGAGENTS_RATING = "tradingagents_rating"
    # Independent of advisory mode.
    MCP_SERVER = "mcp_server"


RECOMMENDATION_FEATURES: frozenset[Feature] = frozenset(
    {
        Feature.SUGGESTION_POOL,
        Feature.PREDICTION_TRACKING,
        Feature.ENTRY_CANDIDATES,
        Feature.STRATEGY_SIGNALS,
        Feature.EVALUATIONS,
        Feature.AI_PAPER_TRADING,
        Feature.POSITION_CALCULATOR,
        Feature.SHARE_CARDS,
        Feature.TRADINGAGENTS_RATING,
    }
)

RESTRICTED_CODE = "ADVISORY_MODE_RESTRICTED"


class FeatureRestrictedError(RuntimeError):
    def __init__(self, feature: Feature) -> None:
        super().__init__(f"{feature.value} is disabled in the current advisory mode")
        self.feature = feature


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_feature_enabled(feature: Feature, settings: ComplianceSettings | None = None) -> bool:
    if feature is Feature.MCP_SERVER:
        return _truthy(os.environ.get("MCP_ENABLED"))
    current = settings or get_compliance_settings()
    if feature in RECOMMENDATION_FEATURES:
        return current.recommendations_publishable
    return True


def require_feature(feature: Feature, settings: ComplianceSettings | None = None) -> None:
    if not is_feature_enabled(feature, settings):
        raise FeatureRestrictedError(feature)


def enabled_features(settings: ComplianceSettings | None = None) -> dict[str, bool]:
    return {f.value: is_feature_enabled(f, settings) for f in Feature}
