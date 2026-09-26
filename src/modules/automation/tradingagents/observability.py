"""TradingAgents progress callbacks.

One callback chain:
1. LangChain `BaseCallbackHandler`: captures the real lifecycle of LangGraph nodes, LLM calls and tools
2. `agent.py` injects the same handler into `Propagator.get_graph_args(callbacks=...)`, without parsing debug text

Progress goes to the app's `log_context`; the frontend polls `/api/agents/runs/{trace_id}/progress`
for the aggregated stages. The second half of this file has cost extraction, budget checks and estimates.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

from src.platform.observability import otel
from src.platform.observability.log_context import log_context
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory

logger = logging.getLogger(__name__)

# Progress and budget share this TradingAgents observation entry point; the DB lifecycle stays in agent_runs.
__all__ = [
    "STAGES_ORDER",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "check_budget",
    "estimate_cost",
    "get_today_cache_key",
]


# Default stage mapping: TradingAgents' 4 analysts + debate + risk + PM
STAGES_ORDER = [
    "data_collection",
    "market_analyst",
    "social_analyst",
    "news_analyst",
    "fundamentals_analyst",
    "bull_bear_debate",
    "research_manager",
    "trader",
    "risk_judge",
    "final_decision",
]

# TradingAgents 0.5.0's LangGraph node names don't map one-to-one to UI stage names.
# The aliases live here instead of string checks in every callback branch; an upstream rename only changes this table.
NODE_STAGE_ALIASES = {
    "market_analyst": "market_analyst",
    "sentiment_analyst": "social_analyst",
    "social_analyst": "social_analyst",
    "news_analyst": "news_analyst",
    "fundamentals_analyst": "fundamentals_analyst",
    "bull_researcher": "bull_bear_debate",
    "bear_researcher": "bull_bear_debate",
    "research_manager": "research_manager",
    "trader": "trader",
    "aggressive_analyst": "risk_judge",
    "conservative_analyst": "risk_judge",
    "neutral_analyst": "risk_judge",
    "risk_judge": "risk_judge",
    "portfolio_manager": "final_decision",
    "final_decision": "final_decision",
}


try:
    from langchain_core.callbacks import BaseCallbackHandler as _LCBaseCallbackHandler
    _LANGCHAIN_AVAILABLE = True
except ImportError:  # the module still imports without tradingagents; tests don't need it
    _LANGCHAIN_AVAILABLE = False

    class _LCBaseCallbackHandler:  # type: ignore[no-redef]
        """Fallback stub when langchain_core is not installed."""
        pass


class PanWatchProgressHandler(_LCBaseCallbackHandler):
    """Progress handler compatible with LangChain's BaseCallbackHandler.

    Newer langchain (1.x) validates the callbacks field with pydantic as BaseCallbackHandler instances,
    so this must inherit the upstream base class to be accepted.

    Core hooks:
    - on_llm_start: an LLM call started (infers which analyst is running)
    - on_llm_end: an LLM call ended, with cost
    - on_chain_start/end: LangGraph node switch

    P0 simple implementation: every event is logged with logger.info, tagged with trace_id.
    The frontend filters log_entries by trace_id + event=ta_progress to build the timeline.
    """

    def __init__(
        self,
        trace_id: str,
        agent_name: str = "tradingagents",
        cancel_event: threading.Event | None = None,
    ):
        # langchain_core's BaseCallbackHandler __init__ takes no arguments, so super() is safe
        try:
            super().__init__()
        except TypeError:
            # Some versions want no arguments, some want them; fall back
            pass
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.cancel_event = cancel_event
        self._started_at = time.monotonic()
        self._total_cost = 0.0
        self._completed_stages: set[str] = set()
        # LangChain 1.x's on_chain_end doesn't always carry name/metadata, so the run_id -> node info
        # from start must be kept to map the end event back to the right stage.
        self._chain_runs: dict[str, dict[str, str]] = {}
        self._llm_runs: dict[str, dict[str, str]] = {}
        self._tool_runs: dict[str, dict[str, str]] = {}
        # OTel bridge: the handler is built on the async side (before to_thread); capture the current context here
        # so callbacks on the worker thread attach node/LLM child spans under the root span (None when disabled).
        self._otel_parent = otel.capture_context()
        self._otel_stage_spans: dict[str, Any] = {}
        self._otel_llm_span: Any = None

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._started_at

    def _emit(self, stage: str, action: str, **extra):
        """Write one progress log entry. The frontend reads it by trace_id + event=ta_progress."""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        with log_context(
            trace_id=self.trace_id,
            agent_name=self.agent_name,
            event="ta_progress",
            tags={
                "stage": stage,
                "action": action,
                "elapsed_sec": round(self.elapsed_sec, 2),
                "total_cost_usd": round(self._total_cost, 6),
                **extra,
            },
        ):
            agent = extra.get("agent") or extra.get("langgraph_node") or ""
            detail = f" agent={agent}" if agent else ""
            logger.info(f"[TA progress] stage={stage} action={action}{detail} {extra}")

    def emit(self, stage: str, action: str, **extra) -> None:
        """Emit a progress event in the same format for non-LangChain stages such as data collection."""
        self._emit(stage, action, **extra)

    # ---- LangChain callbacks ----

    # Key point: LLM cost is estimated from tokens by default (deepseek-chat pricing); callers can inject exact prices
    _PRICE_PER_M_PROMPT = 0.14
    _PRICE_PER_M_COMPLETION = 0.28

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_call_count = getattr(self, "_llm_call_count", 0) + 1
        model = ""
        try:
            model = (
                (kwargs.get("invocation_params") or {}).get("model")
                or (serialized or {}).get("name")
                or ""
            )
        except Exception:
            model = ""
        agent = _callback_agent(kwargs, self._chain_runs)
        operation_id = str(kwargs.get("run_id") or f"llm:{self._llm_call_count}")
        self._llm_runs[operation_id] = {"agent": agent, "model": model}
        self._emit(
            "llm_call",
            "llm_start",
            call_n=self._llm_call_count,
            model=model,
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel: one TA LLM call -> a gen_ai child span (following the GenAI semantic conventions).
        self._otel_llm_span = otel.start_detached_span(
            f"chat {model}".strip() if model else "chat",
            parent_context=self._otel_parent,
            attributes={
                otel.GEN_AI_SYSTEM: "tradingagents",
                otel.GEN_AI_OPERATION_NAME: "chat",
                **({otel.GEN_AI_REQUEST_MODEL: model} if model else {}),
            },
        )

    def on_llm_end(self, response, **kwargs):
        # langchain LLMResult.llm_output contains token_usage
        usage = {}
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
        except Exception:
            pass
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        # Accumulate the cost estimate
        cost = (
            prompt_tokens / 1_000_000 * self._PRICE_PER_M_PROMPT
            + completion_tokens / 1_000_000 * self._PRICE_PER_M_COMPLETION
        )
        self.record_cost(cost)
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._llm_runs.pop(operation_id, {})
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "llm_end",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_cost=round(cost, 6),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel: fill in token usage and end the gen_ai span.
        if self._otel_llm_span is not None:
            otel.set_span_attributes(
                self._otel_llm_span,
                {
                    otel.GEN_AI_USAGE_INPUT_TOKENS: int(prompt_tokens),
                    otel.GEN_AI_USAGE_OUTPUT_TOKENS: int(completion_tokens),
                },
            )
            otel.end_span(self._otel_llm_span)
            self._otel_llm_span = None

    def on_chain_start(self, serialized, inputs, **kwargs):
        # LangGraph node switch. The node name comes from kwargs.name/metadata.langgraph_node first,
        # because serialized may only hold the runnable type name in some LangChain versions.
        name = _callback_name(serialized, kwargs)
        stage = _normalize_stage(name)
        if not stage:
            return
        run_id = _run_id(kwargs)
        if run_id:
            self._chain_runs[run_id] = {
                "name": name,
                "stage": stage,
                "parent_run_id": _parent_run_id(kwargs),
            }
        self._emit(
            stage,
            "stage_start",
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=_parent_run_id(kwargs),
        )
        # Only one current-stage node span is kept; repeated parallel/retry nodes still emit progress events,
        # but duplicate spans can't grow the trace tree without bound.
        if stage not in self._otel_stage_spans:
            span = otel.start_detached_span(
                f"tradingagents.stage {stage}",
                parent_context=self._otel_parent,
                attributes={
                    otel.ATTR_TA_STAGE: stage,
                    otel.ATTR_AGENT_NAME: self.agent_name,
                },
            )
            if span is not None:
                self._otel_stage_spans[stage] = span

    def on_chain_end(self, outputs, **kwargs):
        self._finish_chain("stage_end", kwargs)

    def on_llm_error(self, error, **kwargs):
        self._emit(
            "llm_call",
            "llm_error",
            error=str(error)[:200],
            operation_id=str(kwargs.get("run_id") or ""),
        )
        self._emit("error", "llm_error", error=str(error)[:200])

    def on_chain_error(self, error, **kwargs):
        self._finish_chain("stage_error", kwargs, error=str(error)[:200])
        self._emit("error", "chain_error", error=str(error)[:200], run_id=_run_id(kwargs))

    def on_tool_start(self, serialized, input_str, **kwargs):
        """Record the tool the LangGraph ToolNode is running."""
        name = ""
        try:
            name = kwargs.get("name") or (serialized or {}).get("name") or "unknown"
        except Exception:
            name = kwargs.get("name") or "unknown"
        operation_id = str(kwargs.get("run_id") or f"tool:{name}")
        agent = _callback_agent(kwargs, self._chain_runs)
        self._tool_runs[operation_id] = {"agent": agent, "tool": str(name)}
        self._emit(
            "llm_call",
            "tool_start",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_end(self, output, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_end",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_error(self, error, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_error",
            tool=str(name),
            error=str(error)[:200],
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    # ---- Public methods ----

    def record_cost(self, usd: float) -> None:
        self._total_cost += usd

    def _guess_stage(self, serialized: dict, kwargs: dict) -> str:
        name = _callback_name(serialized, kwargs) or "unknown"
        return _normalize_stage(name) or "unknown"

    def _finish_chain(self, action: str, kwargs: dict, **extra: Any) -> None:
        """Find the node by run_id and emit the end event; works even when upstream omits the node name."""
        run_id = _run_id(kwargs)
        record = self._chain_runs.pop(run_id, None) if run_id else None
        name = (record or {}).get("name") or _callback_name(None, kwargs)
        stage = (record or {}).get("stage") or _normalize_stage(name)
        if not stage:
            return
        self._completed_stages.add(stage)
        self._emit(
            stage,
            action,
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=(record or {}).get("parent_run_id") or _parent_run_id(kwargs),
            **extra,
        )
        if action in {"stage_end", "stage_error"}:
            span = self._otel_stage_spans.pop(stage, None)
            if span is not None:
                otel.end_span(span)


def _normalize_stage(name: str) -> str:
    """Normalise a LangGraph node name to one of STAGES_ORDER."""
    n = "_".join(str(name or "").strip().lower().replace("-", " ").split())
    if not n:
        return ""
    if n in NODE_STAGE_ALIASES:
        return NODE_STAGE_ALIASES[n]
    for stage in STAGES_ORDER:
        if stage in n:
            return stage
    return ""


def _callback_name(serialized: Any, kwargs: dict[str, Any]) -> str:
    """Handle the three node sources in LangChain callbacks: name, metadata and serialized."""
    metadata = kwargs.get("metadata") or {}
    return str(
        kwargs.get("name")
        or metadata.get("langgraph_node")
        or (serialized or {}).get("name", "")
        or ""
    ).strip()


def _run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("run_id") or "")


def _parent_run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("parent_run_id") or "")


def _callback_agent(kwargs: dict[str, Any], chain_runs: dict[str, dict[str, str]]) -> str:
    metadata = kwargs.get("metadata") or {}
    agent = str(metadata.get("langgraph_node") or kwargs.get("name") or "").strip()
    if agent:
        return agent
    parent = chain_runs.get(_parent_run_id(kwargs))
    return str((parent or {}).get("name") or "")


def aggregate_progress(log_entries: list[dict]) -> dict:
    """Read event=ta_progress rows from log_entries and aggregate them into stage progress.

    log_entries row shape (see src/web/log_handler.py):
    {timestamp, level, logger_name, message, trace_id, agent_name, event, tags, ...}
    tags is a dict with stage / action / elapsed_sec / total_cost_usd, etc.

    Returns (for the frontend):
    {
        "current_stage": "bull_bear_debate",
        "completed_stages": [...],
        "started_at": ...,
        "elapsed_sec": 123.4,
        "total_cost_usd": 0.018,
        "stages": [
            {"name": "market_analyst", "status": "done", "duration_sec": 12.3, "cost_usd": 0.004},
            ...
        ]
    }
    """
    stage_state: dict[str, dict] = {s: {"name": s, "status": "pending"} for s in STAGES_ORDER}
    total_cost = 0.0
    current_stage = None
    active_operations: dict[str, dict] = {}
    started_at = None
    collection_sources: dict[str, dict] = {}

    for entry in log_entries:
        tags = entry.get("tags") or {}
        stage = tags.get("stage")
        action = tags.get("action") or ""
        source = tags.get("source")
        ts = entry.get("timestamp")
        if started_at is None and ts:
            started_at = ts

        if not stage or stage not in stage_state:
            # LLM/tool events aren't stages of their own, but the current operation is kept
            # so the UI can name the tool when an external data request hangs.
            if stage == "llm_call":
                kind = "tool" if action.startswith("tool_") else "llm"
                name = tags.get("tool") if kind == "tool" else tags.get("model")
                operation_id = str(tags.get("operation_id") or f"{kind}:{name or action}")
                agent = str(tags.get("agent") or tags.get("langgraph_node") or "")
                if action == "llm_start":
                    operation = {"kind": "llm", "name": tags.get("model") or "LLM call"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action == "tool_start":
                    operation = {"kind": "tool", "name": tags.get("tool") or "Tool call"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action in {"llm_end", "tool_end", "llm_error", "tool_error"}:
                    if tags.get("operation_id"):
                        active_operations.pop(operation_id, None)
                    else:
                        # For old logs or callbacks without run_id: remove only one operation of the same
                        # kind and name, leaving other tools running in parallel alone.
                        expected_name = name or ("Tool call" if kind == "tool" else "LLM call")
                        for key, operation in list(active_operations.items()):
                            if operation["kind"] == kind and operation["name"] == expected_name:
                                active_operations.pop(key, None)
                                break
            continue

        if stage == "data_collection" and source:
            source_state = collection_sources.setdefault(
                source,
                {"name": source, "status": "pending"},
            )
            if action == "source_start":
                source_state["status"] = "running"
            elif action == "source_end":
                source_state["status"] = "done"
            elif action == "source_error":
                source_state["status"] = "error"
                if tags.get("error"):
                    source_state["error"] = str(tags["error"])[:200]

        # cost accumulates; take total_cost_usd from the last entry
        cost = tags.get("total_cost_usd")
        if cost is not None:
            total_cost = max(total_cost, float(cost))

        if action == "stage_start":
            stage_state[stage]["status"] = "running"
            stage_state[stage]["started_at"] = ts
            current_stage = stage
        elif action == "stage_end":
            stage_state[stage]["status"] = "done"
            if "started_at" in stage_state[stage] and ts:
                # rough duration (ts is really a datetime; the caller converts it)
                pass

    return {
        "current_stage": current_stage,
        "completed_stages": [s for s, v in stage_state.items() if v["status"] == "done"],
        "started_at": started_at,
        "elapsed_sec": float(log_entries[-1].get("tags", {}).get("elapsed_sec", 0))
        if log_entries
        else 0,
        "total_cost_usd": round(total_cost, 6),
        "active_operation": next(reversed(active_operations.values()), None)
        if active_operations
        else None,
        "stages": [stage_state[s] for s in STAGES_ORDER],
        "data_sources": list(collection_sources.values()),
    }


# ============================================================================
# Cost and budget tracking
# ============================================================================


def check_budget(monthly_budget_usd: float, agent_name: str = "tradingagents") -> dict:
    """This month's spend in USD plus what's left, checked before a run.

    Returns:
        {
            "used": float,           # spent this month (USD)
            "remaining": float,      # left (USD)
            "limit": float,          # configured limit
            "exceeded": bool,        # over the limit?
            "runs_this_month": int,  # runs this month
        }
    """
    now = datetime.now(timezone.utc)
    # AnalysisHistory.analysis_date is a "YYYY-MM-DD" string
    month_prefix = now.strftime("%Y-%m")

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == agent_name,
                AnalysisHistory.analysis_date.like(f"{month_prefix}-%"),
            )
            .all()
        )

        total = 0.0
        for r in records:
            cost = _extract_cost(r.raw_data)
            if cost:
                total += cost

        used = round(total, 4)
        remaining = max(0.0, float(monthly_budget_usd) - used)
        return {
            "used": used,
            "remaining": round(remaining, 4),
            "limit": float(monthly_budget_usd),
            "exceeded": used >= float(monthly_budget_usd),
            "runs_this_month": len(records),
        }
    except Exception as e:
        logger.warning(f"[TA cost] budget query failed; allowing the run: {e}")
        return {
            "used": 0.0,
            "remaining": float(monthly_budget_usd),
            "limit": float(monthly_budget_usd),
            "exceeded": False,
            "runs_this_month": 0,
        }
    finally:
        db.close()


def _extract_cost(raw_data) -> float:
    """Extract cost_usd from AnalysisHistory.raw_data."""
    if not isinstance(raw_data, dict):
        return 0.0
    cost = raw_data.get("cost_usd")
    if cost is None:
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


def estimate_cost(
    *,
    debate_rounds: int,
    selected_analysts: list[str],
    model: str = "deepseek-chat",
) -> dict:
    """Rough cost estimate for one analysis (can be off by ±50%).

    Shown to the user before a run. Assumptions:
    - each analyst ~5k input + 2k output tokens
    - each debate round ~12k input + 4k output tokens
    - risk + PM ~15k input + 3k output tokens
    - LangGraph's accumulated context is really 2-5x the theoretical size
    """
    n_analysts = len(selected_analysts or [])
    prompt_tokens = n_analysts * 5000 + max(1, debate_rounds) * 12000 + 15000
    completion_tokens = n_analysts * 2000 + max(1, debate_rounds) * 4000 + 3000

    # Price table (USD per million tokens)
    PRICING = {
        "deepseek-chat": (0.14, 0.28),
        "deepseek-reasoner": (0.55, 2.19),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet-4": (3.00, 15.00),
        "glm-4-flash": (0.05, 0.20),
    }
    input_rate, output_rate = PRICING.get(model.lower(), PRICING["deepseek-chat"])
    cost = (prompt_tokens / 1_000_000 * input_rate) + (
        completion_tokens / 1_000_000 * output_rate
    )

    return {
        "model": model,
        "prompt_tokens_est": prompt_tokens,
        "completion_tokens_est": completion_tokens,
        "cost_low_usd": round(cost * 2, 4),
        "cost_high_usd": round(cost * 5, 4),
    }


def get_today_cache_key(symbol: str, market: str, debate_rounds: int, model: str) -> str:
    """Cache key for the same symbol on the same day, to skip repeated LLM calls."""
    today = date.today().isoformat()
    return f"{market}:{symbol}:{today}:r{debate_rounds}:{model}"
