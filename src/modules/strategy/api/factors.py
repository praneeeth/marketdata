"""Factor weight API (M5): read-only list + manual overrides (pin / set weight / toggle auto-calibration).

ResponseWrapperMiddleware wraps responses as {code,data,message}; routes return the raw data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.strategy.factor_weights import get_all_factor_weights, set_factor_weight
from src.platform.persistence.database import get_db

router = APIRouter()


class FactorWeightUpdate(BaseModel):
    """Manual override input; every field is optional (send only what changes)."""

    weight: float | None = None
    is_pinned: bool | None = None
    auto_calibrate: bool | None = None


@router.get("/weights")
def list_weights(db: Session = Depends(get_db)):
    """Weights for every market x factor + the latest IC/IR observation."""
    return {"items": get_all_factor_weights(db=db)}


@router.post("/weights/{factor_code}/{market}")
def update_weight(
    factor_code: str, market: str, payload: FactorWeightUpdate,
    db: Session = Depends(get_db),
):
    """Manually override a factor weight / pin it / toggle auto-calibration (weight changes are audited as manual)."""
    try:
        return set_factor_weight(
            factor_code, market,
            weight=payload.weight, is_pinned=payload.is_pinned,
            auto_calibrate=payload.auto_calibrate, db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
