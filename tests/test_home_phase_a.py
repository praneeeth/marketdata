"""Home page Phase A: today's alert triggers aggregated + portfolio to-dos (for the empty state)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M
from src.modules.portfolio.api import accounts as accounts_api
from src.modules.market.api import price_alerts as alerts_api
from src.platform.persistence.database import Base


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _seed(s):
    acc = M.Account(name="Test", available_funds=1000, enabled=True)
    s.add(acc)
    s.flush()
    mt = M.Stock(symbol="INFY", name="Infosys", market="IN")
    pa = M.Stock(symbol="HDFCBANK", name="HDFC Bank", market="IN")
    s.add_all([mt, pa])
    s.flush()
    s.add(M.Position(account_id=acc.id, stock_id=mt.id, cost_price=1700, quantity=100))
    s.add(M.Position(account_id=acc.id, stock_id=pa.id, cost_price=10, quantity=1000))
    rule = M.PriceAlertRule(stock_id=mt.id, name="Infosys breakdown", enabled=True)  # only Infosys has an alert
    s.add(rule)
    s.flush()
    s.commit()
    return acc, mt, pa, rule


def test_todos_flags_holding_without_alert(db):
    """Held stocks without an alert go into the to-dos; ones with an alert don't."""
    _, mt, pa, _ = _seed(db)
    res = accounts_api.portfolio_todos(db=db)
    msgs = [t["message"] for t in res["todos"]]
    assert any("HDFC Bank" in m for m in msgs), msgs
    assert not any("Infosys" in m for m in msgs), msgs


def test_today_hits_aggregate_with_stock_name(db):
    """Today's triggers are aggregated across rules, with the stock name/symbol."""
    _, mt, pa, rule = _seed(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(
        M.PriceAlertHit(
            rule_id=rule.id,
            stock_id=mt.id,
            trigger_time=now,
            trigger_bucket="bkt1",
            trigger_snapshot={"current_price": 1650},
        )
    )
    db.commit()
    res = alerts_api.list_today_hits(db=db)
    assert len(res) == 1
    assert res[0]["symbol"] == "INFY"
    assert res[0]["name"] == "Infosys"
    assert res[0]["rule_name"] == "Infosys breakdown"


def test_today_hits_excludes_old(db):
    """Triggers from yesterday or earlier don't count for today."""
    _, mt, pa, rule = _seed(db)
    old = datetime(2020, 1, 1)
    db.add(
        M.PriceAlertHit(
            rule_id=rule.id, stock_id=mt.id, trigger_time=old, trigger_bucket="old", trigger_snapshot={}
        )
    )
    db.commit()
    assert alerts_api.list_today_hits(db=db) == []
