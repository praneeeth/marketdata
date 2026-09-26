"""Factor weight storage (M1).

Turns each factor's weight in signal combination from a hard-coded implicit 1 into an external, calibratable value:
- `get_factor_weights(market)`: reads a market's factor weights, lazily seeding missing ones as 1.0;
  `strategy_engine._compute_factor_breakdown` multiplies by them when combining raw_score.

Calibration is in `factor_calibration.py`; the read-only/manual override API is in `web/api/factors.py`.
"""

from __future__ import annotations

import logging

from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# Calibratable factors (matching StrategyFactorSnapshot columns and factor_eval.FACTOR_FIELDS).
# source_bonus isn't calibrated yet (v1 keeps weight 1.0); final_score is the combined result, not an input factor.
CALIBRATABLE_FACTORS = (
    "alpha_score",
    "catalyst_score",
    "quality_score",
    "risk_penalty",
    "crowd_penalty",
)

# Penalty factors: subtracted in raw_score; IC expected to be negative.
PENALTY_FACTORS = frozenset({"risk_penalty", "crowd_penalty"})

MARKETS = ("IN",)


def get_factor_weights(market: str, *, db=None) -> dict[str, float]:
    """Read a market's calibratable factor weights; missing factors are lazily seeded as 1.0.

    Returns {factor_code: weight}; the keys are always the full CALIBRATABLE_FACTORS set.
    Consumers should use `.get(code, 1.0)` for unregistered factors.
    """
    own = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(FactorWeight).filter(FactorWeight.market == market).all()
        existing = {r.factor_code: float(r.weight) for r in rows}
        missing = [f for f in CALIBRATABLE_FACTORS if f not in existing]
        if missing:
            for f in missing:
                db.add(FactorWeight(factor_code=f, market=market, weight=1.0))
            db.commit()
            for f in missing:
                existing[f] = 1.0
        return {f: existing.get(f, 1.0) for f in CALIBRATABLE_FACTORS}
    finally:
        if own:
            db.close()


def _serialize(row: FactorWeight) -> dict:
    meta = row.meta or {}
    return {
        "factor_code": row.factor_code,
        "market": row.market,
        "weight": round(float(row.weight), 4),
        "is_pinned": bool(row.is_pinned),
        "auto_calibrate": bool(row.auto_calibrate),
        "last_ic": meta.get("last_ic"),
        "last_ir": meta.get("last_ir"),
        "last_sample_size": meta.get("last_sample_size"),
        "last_calibrated_at": meta.get("last_calibrated_at"),
        "reason": row.reason or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_all_factor_weights(*, db=None) -> list[dict]:
    """List weights for every market x factor (with the latest IC/IR observation), for the read-only API/UI."""
    own = db is None
    db = db or SessionLocal()
    try:
        for m in MARKETS:
            get_factor_weights(m, db=db)  # make sure every market is seeded
        rows = (
            db.query(FactorWeight)
            .order_by(FactorWeight.market, FactorWeight.factor_code)
            .all()
        )
        return [_serialize(r) for r in rows]
    finally:
        if own:
            db.close()


def set_factor_weight(
    factor_code: str, market: str, *,
    weight: float | None = None, is_pinned: bool | None = None,
    auto_calibrate: bool | None = None, db=None,
) -> dict:
    """Manually override a factor weight / pin it / toggle auto-calibration; weight changes are audited as manual."""
    if factor_code not in CALIBRATABLE_FACTORS:
        raise ValueError(f"Unknown factor: {factor_code}")
    if market not in MARKETS:
        raise ValueError(f"Unknown market: {market}")
    own = db is None
    db = db or SessionLocal()
    try:
        get_factor_weights(market, db=db)  # make sure the row exists
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == factor_code, FactorWeight.market == market)
            .first()
        )
        if weight is not None:
            old = float(row.weight)
            new = round(max(0.1, min(3.0, float(weight))), 4)  # manual values are bounded too, against typos
            if abs(new - old) >= 1e-9:
                row.weight = new
                row.reason = "manual"
                row.effective_from = utc_now()
                row.updated_at = utc_now()
                db.add(FactorWeightHistory(
                    factor_code=factor_code, market=market,
                    old_weight=old, new_weight=new, sample_size=0, reason="manual",
                ))
        if is_pinned is not None:
            row.is_pinned = bool(is_pinned)
            row.updated_at = utc_now()
        if auto_calibrate is not None:
            row.auto_calibrate = bool(auto_calibrate)
            row.updated_at = utc_now()
        db.commit()
        return _serialize(row)
    finally:
        if own:
            db.close()
