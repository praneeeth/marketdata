"""TradingAgents runtime adapter: LLM config, key injection and LangChain compatibility patches.

Bridges the app's AIClient config to the TradingAgents LLM config.

TradingAgents drives LLMs through langchain-openai / langchain-anthropic and similar,
reading a config dict plus environment variables (`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`, etc.).
This module bridges the app's AIClient config across.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.platform.ai.ai_client import AIClient

logger = logging.getLogger(__name__)

# The stable runtime interface the agent entry point may depend on; the compatibility patches live in the second half of this file.
__all__ = [
    "VALID_ANALYSTS",
    "apply_compat_patches",
    "build_ta_llm_config",
    "inject_api_key_env",
]


# Valid values for TradingAgents' selected_analysts field (see upstream graph/trading_graph.py)
VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}


def build_ta_llm_config(
    ai_client: AIClient,
    *,
    debate_rounds: int = 1,
    selected_analysts: list[str] | None = None,
    output_language: str = "Chinese",
    deep_model: str | None = None,
    quick_model: str | None = None,
    market: str = "",
    enable_sec_edgar: bool = False,
    runtime_dir: str | Path | None = None,
    holding_period_days: int = 5,
    llm_timeout_seconds: int = 120,
    llm_max_retries: int = 0,
    llm_max_tokens: int = 4096,
) -> dict[str, Any]:
    """Build the config dict TradingAgents expects.

    Starts from tradingagents.default_config.DEFAULT_CONFIG (which has the required data_cache_dir / project_dir /
    memory_log_path fields), then overrides with the app's config:
    - llm_provider: always the openrouter-compatible protocol (chat completions, avoiding the OpenAI Responses API)
    - backend_url: base_url of the app's AI service
    - deep_think_llm: the "strong model" for reasoning/debate/risk/PM. Defaults to ai_client.model;
      deep_model can override it, e.g. an expensive but accurate model such as claude-sonnet / o3 for debate
    - quick_think_llm: the "fast model" for analyst tool calls. Defaults to deep_model;
      quick_model can override it, e.g. a cheap model such as haiku / gpt-4o-mini for analysts
    - max_debate_rounds: number of debate rounds
    - selected_analysts: ["market", "social", "news", "fundamentals"]
    - output_language: "Chinese" / "English"

    Note: upstream TA uses one backend_url for deep + quick, so both models must sit behind **the same endpoint**.
    To mix Claude + GPT, use a LiteLLM proxy that aggregates providers behind one endpoint.
    """
    analysts = list(selected_analysts or VALID_ANALYSTS)
    invalid = [a for a in analysts if a not in VALID_ANALYSTS]
    if invalid:
        raise ValueError(
            f"Invalid analyst names: {invalid}; valid values: {sorted(VALID_ANALYSTS)}"
        )

    # Start from the upstream default config (data_cache_dir / project_dir / memory_log_path, etc.),
    # otherwise TradingAgentsGraph.__init__ raises KeyError on os.makedirs(config["data_cache_dir"]).
    try:
        from tradingagents.default_config import DEFAULT_CONFIG as _UPSTREAM_DEFAULT
        config = dict(_UPSTREAM_DEFAULT)
    except ImportError:
        config = {}

    # The upstream config has nested vendor settings; copy it so one run can't pollute DEFAULT_CONFIG.
    config["data_vendors"] = dict(config.get("data_vendors") or {})
    config["tool_vendors"] = dict(config.get("tool_vendors") or {})

    if runtime_dir is not None:
        root = Path(runtime_dir).expanduser().resolve()
        results_dir = root / "results"
        data_cache_dir = root / "cache"
        memory_dir = root / "memory"
        for directory in (results_dir, data_cache_dir, memory_dir):
            directory.mkdir(parents=True, exist_ok=True)
        config.update({
            "results_dir": str(results_dir),
            "data_cache_dir": str(data_cache_dir),
            "memory_log_path": str(memory_dir / "trading_memory.md"),
        })

    # SEC EDGAR's three financial statements carry filing-date semantics; they are the first choice only for
    # US stocks when the user enables them explicitly, falling back to yfinance otherwise.
    statement_vendor = "sec_edgar,yfinance" if enable_sec_edgar and market.upper() == "US" else "yfinance"
    # set_config() merges nested dicts. Even when EDGAR is off, yfinance must be written back explicitly
    # so tool_vendors left by an earlier US run can't leak into other analyses.
    config["tool_vendors"].update({
        "get_balance_sheet": statement_vendor,
        "get_cashflow": statement_vendor,
        "get_income_statement": statement_vendor,
    })

    # App overrides.
    # ⚠️ llm_provider deliberately isn't "openai": when TA sees openai it forces use_responses_api=True
    # (the OpenAI Responses API, /v1/responses), which third-party OpenAI-compatible services such as
    # SiliconFlow, Zhipu or Ollama don't support, so they return 404.
    # "openrouter" uses standard chat completions (/v1/chat/completions), and backend_url
    # replaces the default openrouter endpoint with the app's configured base_url.
    # Two-model resolution:
    # - no deep_model -> ai_client.model
    # - no quick_model -> deep_model (single-model case)
    deep_llm = (deep_model or ai_client.model or "").strip() or ai_client.model
    quick_llm = (quick_model or deep_llm or "").strip() or deep_llm

    config.update({
        "llm_provider": "openrouter",
        "backend_url": ai_client.base_url,
        "deep_think_llm": deep_llm,
        "quick_think_llm": quick_llm,
        "max_debate_rounds": max(1, int(debate_rounds)),
        "max_risk_discuss_rounds": 1,
        "selected_analysts": analysts,
        "output_language": output_language,
        "online_tools": True,
        "checkpoint_enabled": False,  # avoid stray sqlite checkpoint files
        "holding_period_days": max(1, int(holding_period_days)),
        # TradingAgents 0.5.0 leaves these to the underlying SDK; without limits, a dropped provider
        # connection or a model that keeps generating can leave the whole LangGraph stuck on one analyst.
        "llm_timeout_seconds": max(1, int(llm_timeout_seconds)),
        "llm_max_retries": max(0, int(llm_max_retries)),
        "max_tokens": max(256, int(llm_max_tokens)),
    })
    return config


def inject_api_key_env(ai_client: AIClient) -> None:
    """Inject the app's AI service API key into environment variables.

    TradingAgents llm_clients read a different env var per provider
    (OPENAI_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY, etc.).
    The app uses openrouter-compatible mode (chat completions), so it injects
    OPENROUTER_API_KEY, and also sets OPENAI_API_KEY as a fallback.

    Note: these are process-wide env vars; concurrent requests with different keys in one process could race.
    P0 assumes max_workers=2 and a single AI service, which is acceptable.
    """
    if not ai_client.api_key:
        logger.warning("[TA] AIClient has no api_key; TradingAgents LLM calls will most likely fail")
        return
    # Set several candidate env vars so TA finds the key whichever provider branch it takes
    os.environ["OPENROUTER_API_KEY"] = ai_client.api_key
    os.environ["OPENAI_API_KEY"] = ai_client.api_key
    os.environ["DEEPSEEK_API_KEY"] = ai_client.api_key


# ============================================================================
# LangChain compatibility patches
# ============================================================================

_PATCH_APPLIED = False


def apply_compat_patches() -> None:
    """Apply all LangChain compatibility patches. Idempotent."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    _patch_tool_call_args_coercion()
    _patch_ai_message_init()
    _PATCH_APPLIED = True


def _coerce_tool_calls_args(tool_calls: Any) -> Any:
    """Turn each item's args in a tool_calls list into a dict when it is a JSON string."""
    if not isinstance(tool_calls, list):
        return tool_calls
    fixed = []
    for tc in tool_calls:
        if isinstance(tc, dict) and "args" in tc:
            raw = tc.get("args")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        tc = {**tc, "args": parsed}
                    else:
                        tc = {**tc, "args": {}}
                except (json.JSONDecodeError, TypeError):
                    tc = {**tc, "args": {}}
        fixed.append(tc)
    return fixed


def _patch_ai_message_init() -> None:
    """Patch AIMessage.__init__ so tool_calls coerce str args -> dict before validation.

    Intercepting AIMessage construction directly is reliable, whichever upstream function built the tool_calls.
    """
    try:
        from langchain_core.messages.ai import AIMessage
    except ImportError:
        return

    if getattr(AIMessage, "_candlewise_patched", False):
        return

    original_init = AIMessage.__init__

    def _patched_init(self, *args, **kwargs):
        if "tool_calls" in kwargs:
            kwargs["tool_calls"] = _coerce_tool_calls_args(kwargs["tool_calls"])
        return original_init(self, *args, **kwargs)

    AIMessage.__init__ = _patched_init  # type: ignore[method-assign]
    AIMessage._candlewise_patched = True  # type: ignore[attr-defined]
    logger.info("[TA compat] Patched AIMessage.__init__ to accept string tool_calls.args")


def _patch_tool_call_args_coercion() -> None:
    """Let ToolCall / AIMessage accept string args and json.loads them automatically."""
    try:
        from langchain_core.messages import tool as _tool_module
    except ImportError:
        logger.debug("[TA compat] langchain_core not installed; skipping the tool_call patch")
        return

    # Find the create_tool_call factory (langchain 1.x); older versions may construct the ToolCall class directly
    create_func = getattr(_tool_module, "create_tool_call", None)
    if create_func is None:
        logger.debug("[TA compat] create_tool_call not found; skipping")
        return

    if getattr(create_func, "_candlewise_patched", False):
        return  # already patched

    original = create_func

    def _patched_create_tool_call(*args, **kwargs):
        # Pull out the args argument (positional or keyword)
        raw_args = kwargs.get("args")
        if raw_args is None and len(args) >= 2:
            # Positional: assumes the create_tool_call(name, args, ...) order
            # (see langchain_core.messages.tool for the real signature; handled loosely here)
            try:
                # Rebuild kwargs so the strict upstream validator gets a dict
                pass
            except Exception:
                pass

        # Fix the args type
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    kwargs["args"] = parsed
                    logger.debug(
                        f"[TA compat] tool_call.args string parsed into a dict "
                        f"(original length {len(raw_args)})"
                    )
                else:
                    kwargs["args"] = {}
            except (json.JSONDecodeError, TypeError):
                kwargs["args"] = {}
                logger.debug("[TA compat] tool_call.args is not valid JSON; falling back to {}")

        return original(*args, **kwargs)

    _patched_create_tool_call._candlewise_patched = True  # type: ignore[attr-defined]

    # Replace the module-level symbol and the internal import
    _tool_module.create_tool_call = _patched_create_tool_call
    try:
        # langchain_core.output_parsers.openai_tools does `from . import create_tool_call` at the top,
        # and import binds the object locally, so it has to be replaced there too
        from langchain_core.output_parsers import openai_tools as _ot
        if hasattr(_ot, "create_tool_call"):
            _ot.create_tool_call = _patched_create_tool_call
    except ImportError:
        pass

    logger.info("[TA compat] Patched langchain_core.messages.tool.create_tool_call")


def _patch_ai_message_validator() -> None:
    """Fallback: patch AIMessage.model_validate to clean up str args.

    Not enabled for now; only used if tool_call_coercion isn't enough.
    """
    pass
