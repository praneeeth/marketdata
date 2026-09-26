"""PanWatch back-test module (Phase 0 foundation).

A lightweight, pure-Python, dependency-free event-driven back-test core, serving to:
- validate how existing StrategySignalRun signals really performed
- give Phase 2's factor IC/IR and back-test-driven re-weighting a ground truth
- share the trading cost model with the simulation (Phase 1)

vectorbt is a possible future vectorised upgrade path (see .docs/quant-framework-comparison.md).
"""

from src.modules.strategy.backtest.cost_model import CostConfig, CostModel, DEFAULT_COST_MODEL, Fill
from src.modules.strategy.backtest.data_adapter import PriceBar, from_klines, load_price_history
from src.modules.strategy.backtest.engine import (
    Backtester,
    BacktestResult,
    BTTrade,
    Signal,
    fixed_cash_sizer,
    horizon_return,
)
from src.modules.strategy.backtest import metrics

__all__ = [
    "CostConfig",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "Fill",
    "PriceBar",
    "from_klines",
    "load_price_history",
    "Backtester",
    "BacktestResult",
    "BTTrade",
    "Signal",
    "fixed_cash_sizer",
    "horizon_return",
    "metrics",
]
