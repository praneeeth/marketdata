"""Agent catalog and kind helpers.

Workflow agents are user-facing, schedulable pipelines.
Capability agents are internal/manual tools and should not be auto-scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass


AGENT_KIND_WORKFLOW = "workflow"
AGENT_KIND_CAPABILITY = "capability"

WORKFLOW_AGENT_NAMES: tuple[str, ...] = (
    "premarket_outlook",
    "intraday_monitor",
    "daily_report",
)

CAPABILITY_AGENT_NAMES: tuple[str, ...] = (
    "news_digest",
)


def infer_agent_kind(agent_name: str | None) -> str:
    name = (agent_name or "").strip()
    if name in CAPABILITY_AGENT_NAMES:
        return AGENT_KIND_CAPABILITY
    return AGENT_KIND_WORKFLOW


def is_workflow_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_WORKFLOW


def is_capability_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_CAPABILITY


@dataclass(frozen=True)
class AgentSeedSpec:
    name: str
    display_name: str
    description: str
    enabled: bool
    schedule: str
    execution_mode: str
    kind: str
    visible: bool
    lifecycle_status: str = "active"
    replaced_by: str = ""
    display_order: int = 0
    config: dict | None = None


AGENT_SEED_SPECS: tuple[AgentSeedSpec, ...] = (
    AgentSeedSpec(
        name="premarket_outlook",
        display_name="Pre-market outlook",
        description="Before the open, combines yesterday's analysis with overnight news for a view of the day",
        enabled=False,
        schedule="0 9 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=10,
    ),
    AgentSeedSpec(
        name="intraday_monitor",
        display_name="Intraday monitor",
        description="Watches during market hours; the AI flags signals worth attention",
        enabled=False,
        schedule="*/5 9-15 * * 1-5",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=20,
        config={
            "event_only": True,
            "price_alert_threshold": 3.0,
            "volume_alert_ratio": 2.0,
            "stop_loss_warning": -5.0,
            "take_profit_warning": 10.0,
            "throttle_minutes": 30,
        },
    ),
    AgentSeedSpec(
        name="daily_report",
        display_name="Daily close report",
        description="After the close each day, writes a report with a market review, per-stock review and what to watch tomorrow",
        enabled=True,
        schedule="30 15 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=30,
    ),
    AgentSeedSpec(
        name="news_digest",
        display_name="News digest (capability)",
        description="Internal capability: fetches, deduplicates and groups news; not scheduled on its own",
        enabled=False,
        schedule="",
        execution_mode="batch",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="premarket_outlook,daily_report,intraday_monitor",
        display_order=110,
        config={
            "since_hours": 12,
            "fallback_since_hours": 24,
        },
    ),
    AgentSeedSpec(
        name="tradingagents",
        display_name="TradingAgents deep research",
        description="Multi-agent research framework (fundamentals/sentiment/news/technicals + bull/bear debate + risk + PM). "
        "3-5 minutes and ~$0.05 per run (deepseek-chat). Manual trigger; off by default.",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=40,
        config={
            "analyst_types": ["market", "social", "news", "fundamentals"],
            "debate_rounds": 1,
            "monthly_budget_usd": 10.0,
            "over_budget_action": "reject",
            "cache_ttl_hours": 12,
            "output_language": "English",
            "deep_model": "",       # empty uses the default AI service's model; e.g. "claude-sonnet-4"
            "quick_model": "",      # empty = deep_model; e.g. a cheap model such as "deepseek-chat"
            "timeout_minutes": 15,
            "llm_timeout_seconds": 120,  # timeout per LLM request so an analyst can't block forever
            "llm_max_retries": 0,         # deep research fails fast to a final state instead of retrying in the graph
            "llm_max_tokens": 4096,       # cap model output to avoid gateway idle timeouts
            "emit_paper_trading_signal": False,  # write BUY decisions to StrategySignalRun
                                                  # to drive simulation entries (off by default; the user must opt in)
            "enable_sec_edgar": False,  # US stocks only: prefer SEC EDGAR statements with filing-date semantics
            "holding_period_days": 5,   # default holding period for upstream decision-quality back-tests
        },
    ),
)
