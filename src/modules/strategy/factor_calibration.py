"""Factor self-calibration (M2): feeds the standalone IC/IR into a light factor-weight calibration loop.

Turns the per-factor IC/IR from `factor_eval.evaluate_factor_ic` into sign-aware target weights,
smooths them with an EMA + clamp, and writes them to `FactorWeight` (audited in `FactorWeightHistory`).
Mirrors the mechanism of `strategy_engine.rebalance_strategy_weights`, at factor level.

Design notes (see .docs/factor-self-calibration-design-2026-06-20.md):
- IC must be measured on the raw factors (snapshots store raw values; weights are only applied when combining), or the loop reinforces itself.
- Penalty factors (risk/crowd) are expected to have negative IC: -IC drives them, so an effective penalty gains weight and an ineffective one loses it.
- Only outcomes whose holding period has finished are used (point-in-time), to avoid look-ahead.
"""

from __future__ import annotations

import logging

from src.modules.strategy.factor_eval import evaluate_factor_ic
from src.modules.strategy.factor_weights import (
    CALIBRATABLE_FACTORS,
    MARKETS,
    PENALTY_FACTORS,
    get_factor_weights,
)
from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# Normalisation baselines: a "good" IR / a "meaningful" single-period IC.
IR_REF = 0.5
IC_REF = 0.05


def compute_target(factor_code: str, ic, ir, *, beta: float = 0.4) -> float | None:
    """Target weight from IC/IR; IR first (steadier), falling back to IC; penalty factors flip the sign.

    Returns None when there isn't enough information (both IC and IR missing); skip the factor.
    term is normalised and clamped to [-1, 1]; target = 1 + beta*term.
    """
    if ir is not None:
        term = ir / IR_REF
    elif ic is not None:
        term = ic / IC_REF
    else:
        return None
    if factor_code in PENALTY_FACTORS:
        term = -term  # penalty factor: the more negative the IC, the more it should be trusted
    term = max(-1.0, min(1.0, term))
    return 1.0 * (1.0 + beta * term)


def blend(old: float, target: float, *, alpha: float = 0.35,
          lo: float = 0.5, hi: float = 1.5) -> float:
    """EMA smoothing (no jumps) + clamp to [lo, hi]."""
    new = old * (1.0 - alpha) + target * alpha
    return max(lo, min(hi, new))


def calibrate_factor_weights(
    market: str, *, alpha: float = 0.35, beta: float = 0.4,
    clamp: tuple[float, float] = (0.5, 1.5),
    min_samples: int = 20, horizon: int = 5, days: int = 90, db=None,
) -> dict:
    """Run one factor-weight calibration for a market, writing FactorWeight + FactorWeightHistory.

    Gates: is_pinned / auto_calibrate=False / too few samples / missing IC -> skip (weights unchanged).
    The latest observation (last_ic/ir/sample_size) is always written to FactorWeight.meta for the API;
    History only records adjustments that actually happened (reason=auto), to avoid audit noise during cold start.
    """
    own = db is None
    db = db or SessionLocal()
    try:
        ic_result = evaluate_factor_ic(
            days=days, horizon=horizon, min_samples=min_samples, market=market, db=db
        )
        factors = ic_result.get("factors", {})
        get_factor_weights(market, db=db)  # make sure the 5 factor rows exist

        lo, hi = float(clamp[0]), float(clamp[1])
        changed = 0
        rows_changed: list[dict] = []

        for code in CALIBRATABLE_FACTORS:
            row = (
                db.query(FactorWeight)
                .filter(FactorWeight.factor_code == code, FactorWeight.market == market)
                .first()
            )
            old = float(row.weight)
            stats = factors.get(code, {})
            ic = stats.get("ic")
            ir = stats.get("ir")
            n = int(stats.get("sample_size", 0))

            # Record the latest observation (for the API), whether or not it was adjusted.
            row.meta = {
                **(row.meta or {}),
                "last_ic": ic, "last_ir": ir, "last_sample_size": n,
                "last_calibrated_at": utc_now().isoformat(),
            }

            if row.is_pinned or not row.auto_calibrate:
                continue
            if n < min_samples or ic is None:
                continue
            target = compute_target(code, ic, ir, beta=beta)
            if target is None:
                continue
            new = round(blend(old, target, alpha=alpha, lo=lo, hi=hi), 4)
            if abs(new - old) < 0.01:
                continue

            row.weight = new
            row.reason = f"auto(ic={ic}, ir={ir}, n={n})"
            row.effective_from = utc_now()
            row.updated_at = utc_now()
            db.add(FactorWeightHistory(
                factor_code=code, market=market, old_weight=old, new_weight=new,
                ic=ic, ir=ir, sample_size=n, reason="auto",
                meta={"target": round(target, 4), "alpha": alpha},
            ))
            changed += 1
            rows_changed.append({
                "factor_code": code, "old_weight": old, "new_weight": new, "sample_size": n,
            })

        db.commit()
        return {"market": market, "checked": len(CALIBRATABLE_FACTORS),
                "changed": changed, "rows": rows_changed}
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[Factor calibration] market={market} failed: {e}")
        db.rollback()
        return {"market": market, "checked": 0, "changed": 0, "rows": [], "error": str(e)}
    finally:
        if own:
            db.close()


def calibrate_all_markets(*, db=None, **kwargs) -> dict[str, dict]:
    """Run one factor calibration per market (India only: IN); called by the scheduler after the daily outcome evaluation.

    kwargs pass through to calibrate_factor_weights (alpha/beta/clamp/min_samples/horizon/days).
    """
    own = db is None
    db = db or SessionLocal()
    try:
        return {m: calibrate_factor_weights(m, db=db, **kwargs) for m in MARKETS}
    finally:
        if own:
            db.close()
