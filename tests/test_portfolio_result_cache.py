"""Portfolio benchmark/attribution cache: cached by holdings fingerprint, invalidated when holdings change; empty results aren't cached.

Rebuilding the full NAV (K-lines per stock) is expensive and the home page asks for benchmark/attribution often. Results are cached by a
holdings fingerprint and a hit skips quotes/K-lines; a holdings change alters the fingerprint -> recompute; failures/empty results aren't cached, so transient faults aren't frozen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.portfolio.portfolio_benchmark as pb
import src.platform.persistence.models as M
from src.modules.portfolio.api import accounts as accounts_api
from src.platform.persistence.database import Base

_HOLDINGS = [{"symbol": "INFY", "market": "IN", "quantity": 100, "market_value": 100.0, "fx": 1.0}]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    accounts_api._PORTFOLIO_RESULT_CACHE.clear()
    try:
        yield s
    finally:
        s.close()
        accounts_api._PORTFOLIO_RESULT_CACHE.clear()


def _add_position(db, symbol: str, qty: float):
    acc = db.query(M.Account).first()
    if not acc:
        acc = M.Account(name="t", available_funds=0, enabled=True)
        db.add(acc)
        db.flush()
    st = M.Stock(symbol=symbol, name=symbol, market="IN")
    db.add(st)
    db.flush()
    db.add(M.Position(account_id=acc.id, stock_id=st.id, cost_price=1.0, quantity=qty))
    db.commit()


def test_benchmark_result_cached(db, monkeypatch):
    """A benchmark request for the same holdings is computed once; the second hits the cache."""
    _add_position(db, "INFY", 100)
    calls = {"n": 0}

    def fake_build(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return {"excess_return": 1.23}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    r2 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    assert calls["n"] == 1, f"the second call should hit the cache; computed {calls['n']} times"
    assert r1 == r2 == {"excess_return": 1.23}


def test_benchmark_empty_not_cached(db, monkeypatch):
    """Insufficient data (build returns empty) isn't cached; the next call recomputes."""
    _add_position(db, "INFY", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(db=db)
    accounts_api.portfolio_benchmark(db=db)
    assert calls["n"] == 2, "an empty result mustn't be cached; it should recompute"
    assert r1.get("empty") is True


def test_benchmark_cache_invalidates_on_holdings_change(db, monkeypatch):
    """After holdings change (the fingerprint changes), it recomputes instead of returning the old cache."""
    _add_position(db, "INFY", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return {"excess_return": float(calls["n"])}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    accounts_api.portfolio_benchmark(db=db)  # computed once, cached
    _add_position(db, "HDFCBANK", 50)  # holdings change -> fingerprint changes
    accounts_api.portfolio_benchmark(db=db)  # should recompute
    assert calls["n"] == 2, "the cache should be invalidated after holdings change"


def test_attribution_result_cached(db, monkeypatch):
    """Attribution results are cached by holdings fingerprint too."""
    _add_position(db, "INFY", 100)
    calls = {"n": 0}

    def fake_attr(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return [{"symbol": "INFY", "contribution_pct": 1.0}]

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_attribution", fake_attr)

    r1 = accounts_api.portfolio_attribution(db=db)
    r2 = accounts_api.portfolio_attribution(db=db)
    assert calls["n"] == 1, f"the second call should hit the cache; got {calls['n']} times"
    assert r1 == r2
