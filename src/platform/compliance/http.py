"""HTTP adapters for compliance feature gates."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from src.platform.compliance.features import RESTRICTED_CODE, Feature, is_feature_enabled


def restricted_detail(feature: Feature) -> dict[str, str]:
    # ResponseWrapperMiddleware keeps only numeric codes, so the machine-readable code is
    # also the message prefix; the frontend matches on it.
    return {
        "message": f"{RESTRICTED_CODE}: '{feature.value}' is not available in research-only mode.",
        "feature": feature.value,
    }


def feature_gate(feature: Feature) -> Callable[[], None]:
    """FastAPI dependency that returns HTTP 403 when ``feature`` is disabled."""

    def _dependency() -> None:
        if not is_feature_enabled(feature):
            raise HTTPException(status_code=403, detail=restricted_detail(feature))

    return _dependency
