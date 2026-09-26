"""TradingAgents integration.

Adapts TauricResearch/TradingAgents (a multi-agent research framework) into Candlewise:
- bridges Candlewise's AI service to the TradingAgents LLM config
- injects data from Candlewise's provider layer into TradingAgents' data vendor layer
- maps TradingAgents' final_state to Candlewise's AnalysisResult
- reports progress through LangChain callbacks

Soft dependency: the `tradingagents` library isn't on PyPI; users git clone it and pip install -e.
Without it, TradingAgentsAgent.run() returns a clear error instead of crashing the service.

Detailed design: `.docs/tradingagents/02-technical-design.md`
"""

from src.modules.automation.tradingagents.agent import TradingAgentsAgent
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.observability import (
    CandlewiseProgressHandler,
    aggregate_progress,
    check_budget,
    estimate_cost,
)

__all__ = [
    "TradingAgentsAgent",
    "CandlewiseProgressHandler",
    "aggregate_progress",
    "build_stock_metadata_context",
    "check_budget",
    "estimate_cost",
    "map_state_to_result",
    "patch_instrument_context",
    "to_tradingagents_portfolio",
]
