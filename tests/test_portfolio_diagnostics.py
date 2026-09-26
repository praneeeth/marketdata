"""Unit tests for portfolio diagnostics (Phase 4); pure functions, no DB."""

from src.modules.portfolio.portfolio_diagnostics import diagnose_positions, herfindahl


def test_herfindahl_fully_concentrated():
    """Everything in one stock -> HHI = 1."""
    assert abs(herfindahl([100, 0, 0, 0]) - 1.0) < 1e-9


def test_herfindahl_evenly_diversified():
    """Four equal weights -> HHI = 0.25."""
    assert abs(herfindahl([25, 25, 25, 25]) - 0.25) < 1e-9


def test_diagnose_empty():
    """Empty holdings don't error; the count is 0."""
    assert diagnose_positions([])["position_count"] == 0


def test_diagnose_max_weight_alert():
    """A largest position above 40% triggers a concentration alert."""
    pos = [
        {"market_value": 600, "market": "CN", "strategy_code": "a"},
        {"market_value": 400, "market": "CN", "strategy_code": "b"},
    ]
    r = diagnose_positions(pos)
    assert r["max_weight"] == 0.6
    assert any("concentrat" in a.lower() for a in r["alerts"])


def test_diagnose_by_market_distribution():
    """Market value aggregated by market correctly."""
    pos = [
        {"market_value": 500, "market": "CN"},
        {"market_value": 500, "market": "US"},
    ]
    r = diagnose_positions(pos)
    assert r["by_market"]["CN"] == 500 and r["by_market"]["US"] == 500
    assert r["total_market_value"] == 1000


def test_diagnose_unrealized_pnl_sum():
    """Unrealised P&L totals correctly."""
    pos = [
        {"market_value": 100, "unrealized_pnl": 12.5},
        {"market_value": 100, "unrealized_pnl": -4.0},
    ]
    assert diagnose_positions(pos)["total_unrealized_pnl"] == 8.5
