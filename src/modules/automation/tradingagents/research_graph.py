"""Research-only TradingAgents graph (ADR-005).

The upstream graph ends with Research Manager → Trader → risk debate → Portfolio
Manager, each of which produces a buy/sell/hold decision. In research-only mode we build
a different workflow from the same upstream building blocks:

    analysts → bull/bear debate → neutral research summariser → END

The summariser is registered under the node name "Research Manager" so upstream's
debate router, which returns that name when the debate ends, keeps working. It writes
empty ``investment_plan``/``trader_investment_plan`` and puts the neutral summary in
``final_trade_decision``, which ``TradingAgentsGraph.propagate`` needs to log the run.
No trader, risk or portfolio-manager node is ever constructed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.platform.compliance.prompt_rules import research_rules

SUMMARY_NODE = "Research Manager"
FORBIDDEN_NODES = frozenset(
    {"Trader", "Aggressive Analyst", "Neutral Analyst", "Conservative Analyst", "Portfolio Manager"}
)

SUMMARY_INSTRUCTIONS = """You are a neutral research editor. Using only the analyst reports
and the bull/bear debate below, write a research summary for {company} as of {trade_date}.

{rules}

Write Markdown with exactly these sections:
### Summary
Two or three sentences on what the evidence shows.
### Bull case
Up to four bullet points, the strongest factual arguments for the business.
### Bear case
Up to four bullet points, the strongest factual arguments against.
### Key risks
Up to four bullet points.
### Technical levels (descriptive)
Recent support and resistance and key moving averages, only if present in the reports.
### Upcoming events
Results, dividends, index or policy events mentioned in the reports.
### Sources
The data sources and dates referred to in the reports.

Analyst reports:
{reports}

Bull/bear debate:
{debate}
"""


def _state_text(state: Mapping[str, Any], key: str) -> str:
    value = state.get(key)
    return value if isinstance(value, str) else ""


def build_summary_prompt(state: Mapping[str, Any]) -> str:
    debate_state = state.get("investment_debate_state")
    debate = debate_state.get("history", "") if isinstance(debate_state, Mapping) else ""
    reports = "\n\n".join(
        f"[{label}]\n{text}"
        for label, text in (
            ("Market/technical", _state_text(state, "market_report")),
            ("Sentiment", _state_text(state, "sentiment_report")),
            ("News", _state_text(state, "news_report")),
            ("Fundamentals", _state_text(state, "fundamentals_report")),
        )
        if text
    )
    return SUMMARY_INSTRUCTIONS.format(
        company=_state_text(state, "company_of_interest") or "the company",
        trade_date=_state_text(state, "trade_date") or "today",
        rules=research_rules(),
        reports=reports or "(no analyst reports)",
        debate=debate or "(no debate)",
    )


def create_research_summarizer(llm: Any) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Graph node producing a neutral research summary instead of an investment plan."""

    def research_summary_node(state: Mapping[str, Any]) -> dict[str, Any]:
        response = llm.invoke(build_summary_prompt(state))
        summary = str(getattr(response, "content", response) or "").strip()
        debate_state = state.get("investment_debate_state")
        debate: dict[str, Any] = dict(debate_state) if isinstance(debate_state, Mapping) else {}
        debate["judge_decision"] = ""
        debate["current_response"] = summary
        return {
            "investment_debate_state": debate,
            "investment_plan": "",
            "trader_investment_plan": "",
            "final_trade_decision": summary,
        }

    return research_summary_node


def build_research_only_workflow(setup: Any, selected_analysts: Sequence[str]) -> Any:
    """Build (uncompiled) a research-only workflow from an upstream ``GraphSetup``."""
    from langgraph.graph import START, StateGraph
    from tradingagents.agents import (
        create_bear_researcher,
        create_bull_researcher,
        create_fundamentals_analyst,
        create_market_analyst,
        create_msg_delete,
        create_news_analyst,
        create_sentiment_analyst,
    )
    from tradingagents.agents.utils.agent_states import AgentState
    from tradingagents.graph.analyst_execution import build_analyst_execution_plan
    from tradingagents.graph.setup import DEBATE_PATH_MAP

    plan = build_analyst_execution_plan(selected_analysts)
    quick = setup.quick_thinking_llm
    analyst_factories: dict[str, Callable[[], Any]] = {
        "market": lambda: create_market_analyst(quick),
        "social": lambda: create_sentiment_analyst(quick),
        "news": lambda: create_news_analyst(quick),
        "fundamentals": lambda: create_fundamentals_analyst(quick),
    }

    workflow: Any = StateGraph(AgentState)
    for spec in plan.specs:
        workflow.add_node(spec.agent_node, analyst_factories[spec.key]())
        workflow.add_node(spec.clear_node, create_msg_delete())
        workflow.add_node(spec.tool_node, setup.tool_nodes[spec.key])
    workflow.add_node("Bull Researcher", create_bull_researcher(quick))
    workflow.add_node("Bear Researcher", create_bear_researcher(quick))
    workflow.add_node(SUMMARY_NODE, create_research_summarizer(setup.deep_thinking_llm))

    workflow.add_edge(START, plan.specs[0].agent_node)
    for index, spec in enumerate(plan.specs):
        workflow.add_conditional_edges(
            spec.agent_node,
            getattr(setup.conditional_logic, f"should_continue_{spec.key}"),
            [spec.tool_node, spec.clear_node],
        )
        workflow.add_edge(spec.tool_node, spec.agent_node)
        if index < len(plan.specs) - 1:
            workflow.add_edge(spec.clear_node, plan.specs[index + 1].agent_node)
        else:
            workflow.add_edge(spec.clear_node, "Bull Researcher")
    for debate_node in ("Bull Researcher", "Bear Researcher"):
        workflow.add_conditional_edges(
            debate_node, setup.conditional_logic.should_continue_debate, DEBATE_PATH_MAP
        )
    workflow.set_finish_point(SUMMARY_NODE)
    return workflow


def install_research_only_workflow(graph: Any, selected_analysts: Sequence[str]) -> None:
    """Replace a constructed ``TradingAgentsGraph``'s workflow with the research-only one."""
    workflow = build_research_only_workflow(graph.graph_setup, selected_analysts)
    graph.workflow = workflow
    graph.graph = workflow.compile()


def workflow_node_names(workflow: Any) -> set[str]:
    nodes = getattr(workflow, "nodes", {})
    return {str(name) for name in nodes}
