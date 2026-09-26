"""Unit tests for simulation position sizing (Phase 1); pure functions, no DB/network."""

from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.paper_trading.paper_trading_engine import _compute_quantity, _position_weight


def test_position_weight_tiers():
    """The stronger the signal, the larger the share of money per trade (in bands)."""
    assert _position_weight(90) == 0.25
    assert _position_weight(80) == 0.18
    assert _position_weight(70) == 0.12
    assert _position_weight(50) == 0.08
    assert _position_weight(90) > _position_weight(50)


def test_compute_quantity_respects_budget():
    """Allocated by market budget x strength ratio, bought in multiples of 100 shares without exceeding the budget's share count."""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=1_000_000, cost_model=cm,
    )
    assert qty > 0 and qty % 100 == 0
    assert qty <= 25000  # 25% budget / price 10


def test_compute_quantity_respects_cash():
    """With too little cash, it falls back to an affordable number of lots, and the buy including fees stays within cash."""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=3000, cost_model=cm,
    )
    assert qty % 100 == 0
    if qty > 0:
        outlay = -cm.fill("buy", 10.0, qty).cash_delta
        assert outlay <= 3000


def test_compute_quantity_insufficient_cash_returns_zero():
    """Returns 0 when cash can't buy even one lot (the entry should be skipped)."""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=100.0,
        available_cash=500, cost_model=cm,
    )
    assert qty == 0


def test_engine_imports_ok():
    """After the change, paper_trading_engine imports cleanly (no syntax/circular import errors) and the key symbols exist."""
    import src.modules.paper_trading.paper_trading_engine as e

    assert hasattr(e, "ENGINE")
    assert hasattr(e, "COST_MODEL")
    assert hasattr(e, "_compute_quantity")
