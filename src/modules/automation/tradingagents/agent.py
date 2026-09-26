"""TradingAgentsAgent: Candlewise's BaseAgent subclass wrapping TauricResearch/TradingAgents.

Design:
1. collect() fetches the quote and daily candles from the user's broker concurrently
2. analyze() runs TradingAgentsGraph instead of a single ai_client.chat call
3. route_to_vendor is monkeypatched so TradingAgents reads Candlewise (broker) data
4. progress callback, cost tracker, monthly budget and a same-day cache
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.modules.automation.base import AgentContext, AnalysisResult, BaseAgent
from src.modules.automation.tradingagents.observability import (
    check_budget,
    estimate_cost,
    get_today_cache_key,
)
from src.modules.automation.tradingagents.runtime_support import (
    VALID_ANALYSTS,
    apply_compat_patches,
    build_ta_llm_config,
    inject_api_key_env,
)
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.observability import CandlewiseProgressHandler
from src.modules.automation.tradingagents.decision import (
    map_state_to_research_result,
    map_state_to_result,
)
from src.modules.automation.tradingagents.research_graph import install_research_only_workflow
from src.platform.compliance import Feature, is_feature_enabled
from src.modules.automation.tradingagents.toolkit_adapter import (
    candlewise_data_context,
    patch_route_to_vendor,
)
from src.modules.research.analysis_history import get_analysis, save_analysis

logger = logging.getLogger(__name__)

__all__ = ["TradingAgentsAgent", "TradingAgentsUnavailable"]



class TradingAgentsUnavailable(RuntimeError):
    """tradingagents is not installed, or an upstream API change made it unusable."""


def _bounded_graph_class(graph_cls):
    """Make TradingAgentsGraph pass request limits to the LangChain LLM client.

    TradingAgents 0.5.0 supports ``llm_max_retries``/``max_tokens`` but its
    ``_get_provider_kwargs`` does not read a custom timeout yet. This small subclass
    bridges the gap without patching site-packages and stays compatible once upstream
    supports timeouts itself.
    """

    class BoundedTradingAgentsGraph(graph_cls):
        def _get_provider_kwargs(self):
            kwargs = dict(super()._get_provider_kwargs())
            timeout = self.config.get("llm_timeout_seconds")
            if timeout is not None and timeout != "":
                kwargs["timeout"] = float(timeout)
            return kwargs

    BoundedTradingAgentsGraph.__name__ = (
        f"Bounded{getattr(graph_cls, '__name__', 'TradingAgentsGraph')}"
    )
    return BoundedTradingAgentsGraph


class TradingAgentsAgent(BaseAgent):
    name = "tradingagents"
    display_name = "TradingAgents deep research"
    description = "Multi-agent research framework; 3-5 minutes, about $0.05 per run (deepseek-chat)"

    def __init__(
        self,
        analyst_types: list[str] | None = None,
        debate_rounds: int = 1,
        monthly_budget_usd: float = 10.0,
        over_budget_action: str = "reject",  # reject / warn / continue
        cache_ttl_hours: int = 12,
        output_language: str = "English",
        deep_model: str | None = None,    # stronger model for reasoning and debate (empty: default)
        quick_model: str | None = None,   # faster model for analyst tool calls (empty: deep_model)
        timeout_minutes: int = 30,        # hard timeout for the whole run; 30 minutes since 0.3.0's heavier tool chain
        collection_timeout_seconds: int = 45,  # hard timeout per external data source
        emit_paper_trading_signal: bool = False,  # write BUY decisions to StrategySignalRun to drive simulation (off in research_only)
        enable_sec_edgar: bool = False,   # optional SEC EDGAR filings (US only; unused in the India fork)
        holding_period_days: int = 5,     # upstream holding-period semantics for decision back-tests
        llm_timeout_seconds: int = 120,   # hard timeout per LLM request so the graph can't hang
        llm_max_retries: int = 0,         # no provider retries inside the graph
        llm_max_tokens: int = 4096,       # cap reasoning/report output to avoid gateway idle timeouts
    ):
        # Validate the analyst selection
        analysts = list(analyst_types or sorted(VALID_ANALYSTS))
        invalid = [a for a in analysts if a not in VALID_ANALYSTS]
        if invalid:
            raise ValueError(
                f"Invalid analyst names: {invalid}; "
                f"valid values: {sorted(VALID_ANALYSTS)}"
            )

        self.analyst_types = analysts
        self.debate_rounds = max(1, int(debate_rounds))
        self.monthly_budget_usd = float(monthly_budget_usd)
        self.over_budget_action = over_budget_action
        self.cache_ttl_hours = max(0, int(cache_ttl_hours))
        self.output_language = output_language
        self.deep_model = (deep_model or "").strip() or None
        self.quick_model = (quick_model or "").strip() or None
        self.timeout_minutes = max(1, int(timeout_minutes))
        self.collection_timeout_seconds = max(5, int(collection_timeout_seconds))
        self.emit_paper_trading_signal = bool(emit_paper_trading_signal)
        self.enable_sec_edgar = bool(enable_sec_edgar)
        self.holding_period_days = max(1, int(holding_period_days))
        self.llm_timeout_seconds = max(1, int(llm_timeout_seconds))
        self.llm_max_retries = max(0, int(llm_max_retries))
        self.llm_max_tokens = max(256, int(llm_max_tokens))

        # Optional-dependency check
        self._available, self._import_error = self._check_availability()

    # ---- BaseAgent abstract methods ----

    async def collect(self, context: AgentContext) -> dict:
        """Collect the stock's data from the user's broker, concurrently."""
        if not context.watchlist:
            raise ValueError("TradingAgents needs at least one stock")
        # One stock per run; with several in the watchlist, use the first
        stock = context.watchlist[0]

        trace_id = getattr(context, "_trace_id", "")
        if not isinstance(trace_id, str) or not trace_id:
            trace_id = self._make_trace_id(stock.symbol)
        progress_handler = CandlewiseProgressHandler(trace_id, self.name)
        setattr(context, "_progress_handler", progress_handler)
        progress_handler.emit("data_collection", "stage_start", symbol=stock.symbol)

        async def _source(name: str, fn, fallback):
            progress_handler.emit("data_collection", "source_start", source=name)
            try:
                value = await asyncio.wait_for(
                    asyncio.to_thread(fn),
                    timeout=self.collection_timeout_seconds,
                )
                if name in {"quote", "klines"} and not value:
                    progress_handler.emit(
                        "data_collection",
                        "source_error",
                        source=name,
                        error="empty result",
                    )
                else:
                    progress_handler.emit("data_collection", "source_end", source=name)
                return value
            except Exception as e:
                # 429s, network timeouts and parse errors only affect that source, never the whole analysis.
                logger.warning(f"[TA] Data source {name} failed; using an empty result: {e}")
                progress_handler.emit(
                    "data_collection",
                    "source_error",
                    source=name,
                    error=str(e)[:200],
                )
                return fallback

        try:
            sym = stock.symbol
            from src.platform.marketdata.collectors.kline_collector import (
                KlineCollector,
                kline_source,
            )
            from src.platform.marketdata.marketdata_client import md_quote_rows

            # Quotes and daily candles come from the user's broker (India-only). Capital
            # flow and announcements were China-only sources; Indian equivalents depend
            # on open question Q8 / Phase 4a.
            with kline_source(f"tradingagents:{trace_id}"):
                quote_rows, klines_list = await asyncio.gather(
                    _source("quote", lambda: md_quote_rows([sym]), []),
                    # Enough history for the verified snapshot and the 200-day average;
                    # analysts reuse this instead of fetching 750 days again.
                    _source("klines", lambda: KlineCollector().get_klines(sym, days=750), []),
                )
            cf, events_list = None, []
        except Exception as e:
            logger.warning(f"[TA] Initialising data sources failed; using empty results: {e}")
            quote_rows, klines_list, cf, events_list = [], [], None, []
        quote_dict = dict(quote_rows[0]) if quote_rows else {}
        capital_list = [cf] if cf else []

        # Financial statements came from akshare for A-shares only; Indian fundamentals
        # depend on open question Q8.
        financial: dict | None = None

        # Precompute indicators (MA/MACD/RSI/KDJ/BOLL) for the get_indicators tool
        technical = None
        try:
            from src.platform.marketdata.collectors.kline_collector import KlineCollector
            technical = await _source(
                "technical",
                lambda: KlineCollector(stock.market).get_technical_indicators(
                    stock.symbol,
                    klines=klines_list or None,
                ),
                None,
            )
        except Exception as e:
            logger.debug(f"[TA] Technical indicators unavailable; skipping: {e}")
        progress_handler.emit("data_collection", "stage_end", symbol=stock.symbol)

        return {
            "stock": stock,
            "quote": quote_dict,
            "klines": klines_list,
            "capital_flow": capital_list,
            "events": events_list,
            "financial": financial,
            "technical": technical,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        # Required by BaseAgent, but this agent does not use a single prompt
        return "", ""

    async def run_single(self, context: AgentContext, symbol: str) -> AnalysisResult:
        """Single-stock entry point, called per stock by AgentScheduler.

        Typical use: a scheduled run on the core holdings linked to tradingagents.
        Narrows the watchlist to the given symbol, then runs the standard run().
        """
        # Find the stock
        targets = [s for s in context.watchlist if s.symbol == symbol]
        if not targets:
            raise ValueError(
                f"run_single: symbol={symbol} is not in the watchlist; skipping"
            )

        # Shallow-copy context.config with only this stock in the watchlist
        from copy import copy
        from src.platform.runtime.config import AppConfig

        narrow_config = AppConfig(
            settings=context.config.settings,
            watchlist=targets,
        )
        narrow_context = copy(context)
        narrow_context.config = narrow_config
        return await self.run(narrow_context)

    # ---- analyze: the TradingAgents multi-agent flow ----

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        if not self._available:
            raise TradingAgentsUnavailable(self._import_error)

        stock = data["stock"]
        trace_id = getattr(context, "_trace_id", "") or self._make_trace_id(stock.symbol)
        force_refresh = bool(getattr(context, "_force_refresh", False))

        # 0) Same-day cache (skipped with force_refresh=True)
        if not force_refresh:
            cached = self._try_cache_hit(stock)
            if cached is not None:
                logger.info(
                    f"[TA] Same-day cache hit (agent=tradingagents symbol={stock.symbol})"
                )
                cached.raw_data["from_cache"] = True
                return cached

        # 1) Budget check
        budget = check_budget(self.monthly_budget_usd, self.name)
        if budget["exceeded"]:
            if self.over_budget_action == "reject":
                raise RuntimeError(
                    f"This month's TradingAgents budget is used up "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f}). "
                    f"Raise the budget limit in Settings to continue."
                )
            elif self.over_budget_action == "warn":
                logger.warning(
                    f"[TA] Over budget, but the policy is warn; continuing "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f})"
                )

        # 2) TradingAgents config (deep and quick models)
        from src.platform.persistence.database import DB_PATH
        ta_runtime_dir = Path(DB_PATH).resolve().parent / "tradingagents"
        ta_config = build_ta_llm_config(
            context.ai_client,
            debate_rounds=self.debate_rounds,
            selected_analysts=self.analyst_types,
            output_language=self.output_language,
            deep_model=self.deep_model,
            quick_model=self.quick_model,
            market=stock.market.value,
            enable_sec_edgar=self.enable_sec_edgar,
            runtime_dir=ta_runtime_dir,
            holding_period_days=self.holding_period_days,
            llm_timeout_seconds=self.llm_timeout_seconds,
            llm_max_retries=self.llm_max_retries,
            llm_max_tokens=self.llm_max_tokens,
        )

        # 3) Progress callback
        progress_handler = getattr(context, "_progress_handler", None)
        if not isinstance(progress_handler, CandlewiseProgressHandler):
            progress_handler = CandlewiseProgressHandler(trace_id, self.name)
        cancel_event = threading.Event()
        progress_handler.cancel_event = cancel_event

        # 4) Stock metadata goes into instrument_context; holdings use TradingAgents 0.5.0's native portfolio.
        current_price = (data.get("quote") or {}).get("current_price")
        cur_price_num = current_price if isinstance(current_price, (int, float)) else None
        quote_data = data.get("quote") or {}

        meta_context = build_stock_metadata_context(
            stock_symbol=stock.symbol,
            stock_name=stock.name or "",
            market=stock.market.value,
            current_price=cur_price_num,
            industry=quote_data.get("industry", "") if isinstance(quote_data, dict) else "",
        )
        # 5) Blocking call in a thread pool, with a hard timeout
        try:
            ta_result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_tradingagents_sync,
                    ai_client=context.ai_client,
                    symbol=stock.symbol,
                    market=stock.market.value,
                    ta_config=ta_config,
                    progress_handler=progress_handler,
                    candlewise_data=data,
                    stock_metadata_context=meta_context,
                    portfolio=getattr(context, "portfolio", None),
                    cancel_event=cancel_event,
                ),
                timeout=self.timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            cancel_event.set()
            # Timeout: try to save partial progress for later inspection
            partial_cost = getattr(progress_handler, "_total_cost", 0.0)
            partial_stages = list(getattr(progress_handler, "_completed_stages", set()))
            logger.warning(
                f"[TA] Timed out (>{self.timeout_minutes} minutes). "
                f"Completed stages: {partial_stages}, cost so far ${partial_cost:.4f}"
            )
            partial_msg = (
                f"Analysis timed out (>{self.timeout_minutes} minutes). "
                f"{len(partial_stages)} stages completed, cost so far ${partial_cost:.4f}. "
                f"Try: 1) fewer debate_rounds; 2) a faster model (e.g. deepseek-chat); "
                f"3) a higher timeout_minutes."
            )
            raise RuntimeError(partial_msg)
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        except Exception:
            cancel_event.set()
            raise

        # 5) Map to an AnalysisResult (research-only unless ratings are publishable)
        mapper = (
            map_state_to_result
            if is_feature_enabled(Feature.TRADINGAGENTS_RATING)
            else map_state_to_research_result
        )
        result = mapper(
            stock=stock,
            ta_result=ta_result,
            model_label=context.model_label,
        )

        # Store the price at analysis time so history shows it at once (no wait for the daily close)
        _quote = data.get("quote") or {}
        _cp = _quote.get("current_price")
        if isinstance(_cp, (int, float)):
            result.raw_data["price_at_analysis"] = float(_cp)

        # 5b) Aggregate this trace_id's toolkit diagnostics into raw_data so past reports can
        # show how data was supplied.
        try:
            tid = getattr(progress_handler, "trace_id", "") if progress_handler else ""
            if tid:
                result.raw_data["toolkit_diagnostic"] = self._collect_toolkit_diagnostic(tid)
        except Exception as e:
            logger.warning(f"[TA] Collecting toolkit diagnostics failed; ignoring: {e}")

        # 6) Save to AnalysisHistory: the UI reads the latest result (DeepAnalysisModal) and the
        # monthly budget aggregates it. A rerun for the same stock and day overwrites it.
        try:
            save_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                content=result.content,
                title=result.title,
                raw_data=result.raw_data,
            )
        except Exception as e:
            logger.warning(f"[TA] save_analysis failed; continuing: {e}")

        # 6b) Save to StockSuggestion (the suggestion pool) so badges on the positions and
        # watchlist pages show TradingAgents' result (recommendation mode only).
        try:
            from src.modules.automation.suggestion_pool import save_suggestion

            sug = result.raw_data.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "Hold"
            signal_text = (sug.get("signal") or "")[:500]
            reason_text = (sug.get("reason") or "")[:1000]
            confidence = sug.get("confidence")
            confidence_text = (
                f" (confidence {confidence:.1f}/10)" if isinstance(confidence, (int, float)) else ""
            )

            save_suggestion(
                stock_symbol=stock.symbol,
                stock_name=stock.name,
                stock_market=stock.market.value,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name=self.name,
                agent_label="TradingAgents deep research",
                signal=signal_text,
                reason=reason_text,
                expires_hours=24,  # deep research stays valid for 24 hours
                ai_response=result.content[:2000],
                meta={
                    "cost_usd": result.raw_data.get("cost_usd", 0),
                    "decision": result.raw_data.get("decision", "HOLD"),
                    "confidence": confidence,
                },
            )
        except Exception as e:
            logger.warning(f"[TA] save_suggestion failed; continuing: {e}")

        # Research-only fork: TradingAgents never emits paper-trading signals (ADR-004).

        return result

    # ---- Private methods ----

    def _check_availability(self) -> tuple[bool, str]:
        """Whether tradingagents is available."""
        try:
            import tradingagents  # noqa: F401
            from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
        except ImportError as e:
            return False, (
                "tradingagents is not installed. Run `pip install -r requirements.txt` "
                "(or just `pip install \"tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git\"`). "
                "Behind a corporate proxy, try `env -u HTTP_PROXY -u HTTPS_PROXY pip install -r requirements.txt`. "
                f"Original error: {e}"
            )
        except Exception as e:
            return False, f"Loading tradingagents failed: {e}"
        return True, ""

    def _make_trace_id(self, symbol: str) -> str:
        return f"ta-{symbol}-{int(datetime.now().timestamp())}"

    def _try_cache_hit(self, stock) -> AnalysisResult | None:
        """The cached AnalysisResult if this stock was already analysed today."""
        if self.cache_ttl_hours <= 0:
            return None
        try:
            history = get_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                analysis_date=date.today(),
            )
        except Exception:
            return None
        if not history or not history.raw_data:
            return None
        return AnalysisResult(
            agent_name=self.name,
            title=history.title or f"[Deep research, cached] {stock.name} ({stock.symbol})",
            content=history.content,
            raw_data=dict(history.raw_data),
        )

    def _run_tradingagents_sync(
        self,
        *,
        ai_client,
        symbol: str,
        market: str,
        ta_config: dict,
        progress_handler,
        candlewise_data: dict,
        stock_metadata_context: str = "",
        portfolio: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Run the synchronous TradingAgents flow in a worker thread.

        Steps:
        1. inject_api_key_env puts the API key into the environment
        2. patch_route_to_vendor routes data requests to Candlewise (broker) data
        3. TradingAgentsGraph.propagate runs for 3-5 minutes
        4. returns decision, final_state and cost_usd
        """
        # Import lazily so this is never reached when _check_availability fails
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # LangChain compatibility patch: small models (Qwen 7B etc.) return tool_calls.args as
        # a string; convert it to a dict.
        apply_compat_patches()
        inject_api_key_env(ai_client)

        # Patch plus data context so TradingAgents' route_to_vendor calls get Candlewise data
        trace_id_for_ctx = getattr(progress_handler, "trace_id", "") if progress_handler else ""
        with patch_route_to_vendor(), candlewise_data_context(
            candlewise_data,
            trace_id=trace_id_for_ctx,
            cancel_event=cancel_event,
        ):
            graph = _bounded_graph_class(TradingAgentsGraph)(
                selected_analysts=ta_config["selected_analysts"],
                debug=False,
                config=ta_config,
                # callbacks takes a list of LangChain BaseCallbackHandler; LLM level
                callbacks=[progress_handler] if progress_handler else None,
            )

            # Research-only: never build the trader / risk / portfolio-manager nodes (ADR-005).
            if not is_feature_enabled(Feature.TRADINGAGENTS_RATING):
                install_research_only_workflow(graph, ta_config["selected_analysts"])

            # Inject LangGraph node-level callbacks (propagator.get_graph_args defaults to
            # callbacks=None, so on_chain_start/end never fire and progress stays pending)
            if progress_handler is not None:
                self._inject_graph_callbacks(graph, progress_handler)

            # 0.5.0 provides instrument_context natively; stock metadata must not pollute past_context.
            if stock_metadata_context:
                patch_instrument_context(graph, stock_metadata_context)

            date_str = datetime.now().strftime("%Y-%m-%d")
            final_state, decision = graph.propagate(
                symbol,
                date_str,
                portfolio=to_tradingagents_portfolio(portfolio),
            )

        # Cost (TradingAgents' internal token count; falls back to the estimate if not exposed)
        cost_usd = self._extract_cost_from_graph(graph) or self._fallback_cost_estimate(
            ta_config
        )

        return {
            "decision": str(decision or "HOLD").upper(),
            "final_state": dict(final_state) if final_state else {},
            "cost_usd": float(cost_usd or 0.0),
        }

    @staticmethod
    def _inject_graph_callbacks(graph, handler):
        """Monkey-patch graph.propagator.get_graph_args to inject node-level LangGraph callbacks.

        Otherwise only on_llm_start/end fire, never on_chain_start/end (node changes), and the
        progress bar freezes.
        """
        try:
            propagator = getattr(graph, "propagator", None)
            if propagator is None or not hasattr(propagator, "get_graph_args"):
                return
            original = propagator.get_graph_args

            def _patched(callbacks=None):
                cbs = list(callbacks or [])
                if handler not in cbs:
                    cbs.append(handler)
                return original(callbacks=cbs)

            propagator.get_graph_args = _patched  # type: ignore[method-assign]
        except Exception as e:
            logger.warning(f"[TA] Injecting LangGraph callbacks failed: {e}")

    @staticmethod
    def _collect_toolkit_diagnostic(trace_id: str) -> dict:
        """Aggregate this trace_id's ta_toolkit logs into {summary, recent}."""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import LogEntry

        db = SessionLocal()
        try:
            rows = (
                db.query(LogEntry)
                .filter(LogEntry.trace_id == trace_id, LogEntry.event == "ta_toolkit")
                .order_by(LogEntry.id.asc())
                .all()
            )
        finally:
            db.close()

        summary = {"hit": 0, "miss": 0, "passthrough": 0, "fallthrough": 0, "error": 0}
        recent = []
        for r in rows:
            tags = r.tags or {}
            action = (tags.get("action") or "").lower()
            if action in summary:
                summary[action] += 1
            recent.append({
                "action": tags.get("action"),
                "method": tags.get("method"),
                "symbol": tags.get("symbol"),
                "chars": tags.get("chars"),
                "snippet": tags.get("snippet"),
                "source": tags.get("source"),
                "reason": tags.get("reason"),
            })
        return {"summary": summary, "recent": recent[-50:]}

    @staticmethod
    def _extract_cost_from_graph(graph) -> float:
        """Try to read the accumulated cost from the graph; upstream may not expose it."""
        for attr in ("total_cost", "total_cost_usd", "_total_cost"):
            v = getattr(graph, attr, None)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _fallback_cost_estimate(self, ta_config: dict) -> float:
        """Fall back to the average estimate."""
        est = estimate_cost(
            debate_rounds=ta_config.get("max_debate_rounds", 1),
            selected_analysts=ta_config.get("selected_analysts", []),
            model=ta_config.get("deep_think_llm", "deepseek-chat"),
        )
        return (est["cost_low_usd"] + est["cost_high_usd"]) / 2
