"""Compliance layer: advisory mode, output guard, disclaimers and feature gates.

Research-only is the default and is enforced in code (see docs/adr.md, ADR-002).
"""

from src.platform.compliance.disclaimer import (
    DISCLAIMER_VERSION,
    LONG_DISCLAIMER,
    SHORT_DISCLAIMER,
    SIMULATION_LABEL,
    SIMULATION_NOTICE,
    with_short_disclaimer,
)
from src.platform.compliance.features import (
    RESTRICTED_CODE,
    Feature,
    FeatureRestrictedError,
    enabled_features,
    is_feature_enabled,
    require_feature,
)
from src.platform.compliance.guard import (
    BLOCKED_MESSAGE,
    REDACTION_MARKER,
    GuardedText,
    GuardResult,
    ensure_guarded,
    guard_text,
    guard_title,
    is_guarded,
)
from src.platform.compliance.input_screen import (
    RESEARCH_ONLY_REPLY,
    TURN_INSTRUCTION,
    ScreenResult,
    screen_user_message,
)
from src.platform.compliance.settings import (
    AdvisoryMode,
    ComplianceConfigError,
    ComplianceSettings,
    get_compliance_settings,
    load_compliance_settings,
)
from src.platform.compliance.stream import GuardedTokenStream, guard_chat_stream
from src.platform.compliance.structured import sanitize_payload

__all__ = [
    "BLOCKED_MESSAGE",
    "DISCLAIMER_VERSION",
    "LONG_DISCLAIMER",
    "REDACTION_MARKER",
    "RESEARCH_ONLY_REPLY",
    "RESTRICTED_CODE",
    "SHORT_DISCLAIMER",
    "SIMULATION_LABEL",
    "SIMULATION_NOTICE",
    "TURN_INSTRUCTION",
    "AdvisoryMode",
    "ComplianceConfigError",
    "ComplianceSettings",
    "Feature",
    "FeatureRestrictedError",
    "GuardResult",
    "GuardedText",
    "GuardedTokenStream",
    "ScreenResult",
    "enabled_features",
    "ensure_guarded",
    "get_compliance_settings",
    "guard_chat_stream",
    "guard_text",
    "guard_title",
    "is_feature_enabled",
    "is_guarded",
    "load_compliance_settings",
    "require_feature",
    "sanitize_payload",
    "screen_user_message",
    "with_short_disclaimer",
]
