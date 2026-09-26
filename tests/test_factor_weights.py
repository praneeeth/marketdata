"""Factor weight storage (M1): lazy seed + reads, using an isolated in-memory DB."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  registers every ORM model on Base.metadata
from src.platform.persistence.database import Base


def _mem_db():
    """A fresh in-memory SQLite session per case, so the development DB isn't polluted."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_get_factor_weights_lazy_seeds_defaults():
    """First read for a market: all 5 calibratable factors are lazily seeded as 1.0."""
    from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, get_factor_weights

    db = _mem_db()
    try:
        w = get_factor_weights("IN", db=db)
        assert set(w) == set(CALIBRATABLE_FACTORS)
        assert all(v == 1.0 for v in w.values())
    finally:
        db.close()


def test_get_factor_weights_idempotent_no_dup_rows():
    """Repeated reads don't create duplicate rows (idempotent seed)."""
    from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, get_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        get_factor_weights("IN", db=db)
        get_factor_weights("IN", db=db)
        n = db.query(FactorWeight).filter(FactorWeight.market == "IN").count()
        assert n == len(CALIBRATABLE_FACTORS)
    finally:
        db.close()


def test_get_factor_weights_reads_stored_value():
    """Existing non-default weights are read back, not overwritten by the seed."""
    from src.modules.strategy.factor_weights import get_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        db.add(FactorWeight(factor_code="alpha_score", market="IN", weight=1.3))
        db.commit()
        w = get_factor_weights("IN", db=db)
        assert w["alpha_score"] == 1.3
        # The other factors are still filled in with the default 1.0
        assert w["catalyst_score"] == 1.0
    finally:
        db.close()


def test_get_all_factor_weights_lists_all_markets():
    """Lists every market x factor, with weight/is_pinned/auto_calibrate."""
    from src.modules.strategy.factor_weights import (
        CALIBRATABLE_FACTORS,
        MARKETS,
        get_all_factor_weights,
    )

    db = _mem_db()
    try:
        items = get_all_factor_weights(db=db)
        assert len(items) == len(CALIBRATABLE_FACTORS) * len(MARKETS)
        keys = {(i["factor_code"], i["market"]) for i in items}
        assert ("alpha_score", "IN") in keys
        sample = items[0]
        assert {"weight", "is_pinned", "auto_calibrate"} <= set(sample)
    finally:
        db.close()


def test_set_factor_weight_manual_writes_history():
    """A manual weight change writes a manual audit and can set is_pinned at the same time."""
    from src.modules.strategy.factor_weights import set_factor_weight
    from src.platform.persistence.models import FactorWeightHistory

    db = _mem_db()
    try:
        res = set_factor_weight("alpha_score", "IN", weight=1.3, is_pinned=True, db=db)
        assert res["weight"] == 1.3
        assert res["is_pinned"] is True
        hist = (db.query(FactorWeightHistory)
                .filter_by(factor_code="alpha_score", market="IN", reason="manual").all())
        assert len(hist) == 1
        assert hist[0].new_weight == 1.3
    finally:
        db.close()


def test_set_factor_weight_rejects_unknown_factor():
    """An unknown factor raises ValueError (the API layer turns it into 400)."""
    import pytest

    from src.modules.strategy.factor_weights import set_factor_weight

    db = _mem_db()
    try:
        with pytest.raises(ValueError):
            set_factor_weight("not_a_factor", "IN", weight=1.1, db=db)
    finally:
        db.close()
