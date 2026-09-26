"""Factor self-calibration (M2): the pure functions compute_target / blend + the DB entry point calibrate_factor_weights."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  registers the ORM models
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --------------------------- pure function: compute_target ---------------------------

def test_compute_target_additive_positive_ir():
    """Additive factor + positive IR: target weight > 1 (IR first, clamped to the 1.4 cap)."""
    from src.modules.strategy.factor_calibration import compute_target

    # ir=0.55 → term=1.1 → clamp 1.0 → 1 + 0.4*1.0 = 1.4
    assert abs(compute_target("catalyst_score", ic=0.06, ir=0.55) - 1.4) < 1e-9


def test_compute_target_falls_back_to_ic_when_no_ir():
    """Without IR, fall back to IC."""
    from src.modules.strategy.factor_calibration import compute_target

    # ir=None, ic=0.025 → term=0.5 → 1 + 0.4*0.5 = 1.2
    assert abs(compute_target("alpha_score", ic=0.025, ir=None) - 1.2) < 1e-9


def test_compute_target_penalty_good_negative_ic_raises_weight():
    """Penalty factor with negative IC (the penalty works) -> sign flipped, weight raised."""
    from src.modules.strategy.factor_calibration import compute_target

    # risk_penalty ir=-0.5 -> term=-1.0 -> penalty flip +1.0 -> 1.4
    assert abs(compute_target("risk_penalty", ic=-0.04, ir=-0.5) - 1.4) < 1e-9


def test_compute_target_penalty_failing_positive_ic_lowers_weight():
    """Penalty factor whose IC turned positive (the penalty stopped working) -> sign flipped, weight lowered."""
    from src.modules.strategy.factor_calibration import compute_target

    # risk_penalty ir=+0.5 -> term=1.0 -> flip -1.0 -> 1 - 0.4 = 0.6
    assert abs(compute_target("risk_penalty", ic=0.04, ir=0.5) - 0.6) < 1e-9


def test_compute_target_returns_none_when_no_ic_ir():
    """Both IC and IR missing -> not enough information; returns None (skip the factor)."""
    from src.modules.strategy.factor_calibration import compute_target

    assert compute_target("alpha_score", ic=None, ir=None) is None


# --------------------------- pure function: blend ---------------------------

def test_blend_ema():
    """EMA smoothing: blend(1.0, 1.4, alpha=0.35) = 1.14."""
    from src.modules.strategy.factor_calibration import blend

    assert abs(blend(1.0, 1.4, alpha=0.35) - 1.14) < 1e-9


def test_blend_clamps_high():
    """A blended result above the cap is clamped to hi."""
    from src.modules.strategy.factor_calibration import blend

    assert blend(1.45, 2.0, alpha=0.35, lo=0.5, hi=1.5) == 1.5


def test_blend_clamps_low():
    """A blended result below the floor is clamped to lo."""
    from src.modules.strategy.factor_calibration import blend

    assert blend(0.55, 0.0, alpha=0.35, lo=0.5, hi=1.5) == 0.5


# --------------------------- DB: evaluate_factor_ic market filter / leak guard ---------------------------

def _seed_pair(db, sid, *, market, snapshot_date, alpha=0.0, ret=0.0,
               horizon=5, status="evaluated"):
    """Insert a StrategyFactorSnapshot + StrategyOutcome pair (linked by signal_run_id)."""
    from src.platform.persistence.models import StrategyFactorSnapshot, StrategyOutcome

    db.add(StrategyFactorSnapshot(
        signal_run_id=sid, snapshot_date=snapshot_date, stock_symbol=f"S{sid}",
        stock_market=market, strategy_code="trend_follow",
        alpha_score=alpha, final_score=50.0,
    ))
    db.add(StrategyOutcome(
        signal_run_id=sid, strategy_code="trend_follow", stock_symbol=f"S{sid}",
        stock_market=market, snapshot_date=snapshot_date, horizon_days=horizon,
        target_date=snapshot_date, outcome_return_pct=ret, outcome_status=status,
    ))


def _old_date(days_ago=30):
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_evaluate_factor_ic_filters_by_market():
    """With market given, only that market's samples are counted."""
    from src.modules.strategy.factor_eval import evaluate_factor_ic

    db = _mem_db()
    try:
        d = _old_date()
        for i in range(1, 6):  # 5 CN
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        for i in range(6, 10):  # 4 HK
            _seed_pair(db, i, market="HK", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        res = evaluate_factor_ic(days=90, horizon=5, min_samples=3, market="CN", db=db)
        assert res["factors"]["alpha_score"]["sample_size"] == 5
    finally:
        db.close()


def test_evaluate_factor_ic_excludes_unelapsed_horizon():
    """Samples whose holding period hasn't finished (snapshot = today) are excluded even if marked evaluated (leak guard)."""
    from src.modules.strategy.factor_eval import evaluate_factor_ic

    db = _mem_db()
    try:
        for i in range(1, 5):  # 4 rows, old enough
            _seed_pair(db, i, market="CN", snapshot_date=_old_date(), alpha=float(i), ret=float(i))
        # 1 "today" leaking sample
        _seed_pair(db, 99, market="CN", snapshot_date=date.today().strftime("%Y-%m-%d"),
                   alpha=9.0, ret=9.0)
        db.commit()

        res = evaluate_factor_ic(days=90, horizon=5, min_samples=3, market="CN", db=db)
        assert res["factors"]["alpha_score"]["sample_size"] == 4
    finally:
        db.close()


# --------------------------- DB:calibrate_factor_weights ---------------------------

def test_calibrate_moves_weight_from_ic_and_audits():
    """alpha perfectly correlated with return -> IC=+1 -> weight raised and an auto audit written."""
    from src.modules.strategy.factor_calibration import calibrate_factor_weights
    from src.platform.persistence.models import FactorWeight, FactorWeightHistory

    db = _mem_db()
    try:
        d = _old_date()
        for i in range(1, 7):  # 6 rows; alpha and ret move together monotonically
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        calibrate_factor_weights("CN", min_samples=5, db=db)

        row = db.query(FactorWeight).filter_by(factor_code="alpha_score", market="CN").first()
        assert row.weight > 1.0
        assert abs(row.weight - 1.14) < 0.02

        hist = (db.query(FactorWeightHistory)
                .filter_by(factor_code="alpha_score", market="CN", reason="auto").all())
        assert len(hist) == 1
        assert hist[0].ic is not None
    finally:
        db.close()


def test_calibrate_skips_pinned():
    """A pinned factor isn't touched even with a strong IC."""
    from src.modules.strategy.factor_calibration import calibrate_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        db.add(FactorWeight(factor_code="alpha_score", market="CN", weight=1.0, is_pinned=True))
        db.commit()
        d = _old_date()
        for i in range(1, 7):
            _seed_pair(db, i, market="CN", snapshot_date=d, alpha=float(i), ret=float(i))
        db.commit()

        calibrate_factor_weights("CN", min_samples=5, db=db)

        row = db.query(FactorWeight).filter_by(factor_code="alpha_score", market="CN").first()
        assert row.weight == 1.0
    finally:
        db.close()
