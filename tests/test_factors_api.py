"""Factor weight API (M5): read-only list + manual overrides + route mounting."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  registers the ORM models
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_list_weights_returns_all_market_factor_pairs():
    """GET lists 5 factors for the one market (IN)."""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        res = factors.list_weights(db=db)
        assert "items" in res
        assert len(res["items"]) == 5
    finally:
        db.close()


def test_update_weight_pins_and_sets_value():
    """POST a manual weight + pin."""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        payload = factors.FactorWeightUpdate(weight=1.25, is_pinned=True)
        res = factors.update_weight("alpha_score", "IN", payload, db=db)
        assert res["weight"] == 1.25
        assert res["is_pinned"] is True
    finally:
        db.close()


def test_update_weight_unknown_factor_returns_400():
    """Unknown factor -> HTTP 400."""
    from src.modules.strategy.api import factors

    db = _mem_db()
    try:
        payload = factors.FactorWeightUpdate(weight=1.1)
        with pytest.raises(HTTPException) as ei:
            factors.update_weight("bad_factor", "IN", payload, db=db)
        assert ei.value.status_code == 400
    finally:
        db.close()


def test_factors_router_mounted():
    """/api/factors/weights is mounted on the app (via the OpenAPI schema, compatible with the custom _IncludedRouter)."""
    from src.bootstrap.application import app

    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/factors/weights" in paths
    assert "/api/factors/weights/{factor_code}/{market}" in paths
