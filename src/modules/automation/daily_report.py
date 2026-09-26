import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.modules.automation.research_output import (
    load_research_prompt,
    parse_research_items,
    research_payload,
)
from src.platform.compliance import Feature, is_feature_enabled
from src.modules.research.analysis_history import save_analysis
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.platform.marketdata.models import MarketCode, IndexData

logger = logging.getLogger(__name__)

# Post-market action labels (recommendation mode only; unreachable in research_only)
DAILY_ACTION_MAP = {
    "Keep holding": {"action": "hold", "label": "Keep holding"},
    "Consider adding": {"action": "add", "label": "Consider adding"},
    "Consider reducing": {"action": "reduce", "label": "Consider reducing"},
    "Consider exiting": {"action": "sell", "label": "Consider exiting"},
    "Watch tomorrow": {"action": "watch", "label": "Watch tomorrow"},
    "Avoid for now": {"action": "avoid", "label": "Avoid for now"},
}

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "daily_report.txt"

class DailyReportAgent(BaseAgent):
    """Daily post-market report agent."""

    name = "daily_report"
    display_name = "Daily close report"
    description = "After the close: a watchlist report with the market overview and per-stock research"

    async def _fetch_index_for_market(self, market_code: MarketCode) -> list[IndexData]:
        """Headline Indian indices (NIFTY 50, NIFTY BANK, SENSEX) from the user's broker."""
        from src.platform.marketdata.marketdata_client import md_india_indices

        return await asyncio.to_thread(md_india_indices)

    async def collect(self, context: AgentContext) -> dict:
        """Collect the indices and each stock's structured data (quote, technicals, flows, news, position)."""

        all_indices: list[IndexData] = []
        markets = []
        seen = set()
        for s in context.watchlist:
            if s.market not in seen:
                seen.add(s.market)
                markets.append(s.market)

        for market_code in markets:
            try:
                indices = await self._fetch_index_for_market(market_code)
                all_indices.extend(indices)
            except Exception as e:
                logger.warning(f"Fetching {market_code.value} indices failed: {e}")

        builder = SignalPackBuilder()
        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        packs = await builder.build_for_symbols(
            symbols=sym_list,
            include_news=True,
            news_hours=72,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=7,
        )

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=24,
            extended_hours=72,
            history_days=30,
            kline_days=120,
            persist_snapshot=True,
        )

        if not all_indices and not any(p.quote for p in packs.values()):
            raise RuntimeError("Data collection failed: no quotes were returned. Check the broker connection.")

        return {
            "indices": all_indices,
            "signal_packs": packs,
            "symbol_contexts": context_pack.get("symbols", {}),
            "quality_overview": context_pack.get("quality_overview", {}),
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the daily report prompt."""
        system_prompt = load_research_prompt("daily_report.txt")

        # Helper: safe numeric access; None becomes the default
        def safe_num(value, default=0):
            return value if value is not None else default

        # User input: structured market data
        lines = []
        lines.append(f"## Date: {datetime.now().strftime('%Y-%m-%d')}\n")
        symbol_contexts = data.get("symbol_contexts", {}) or {}
        quality_overview = data.get("quality_overview", {}) or {}

        if quality_overview:
            lines.append("## Context quality")
            lines.append(
                f"- Average quality: {quality_overview.get('avg_score', 0)} (min {quality_overview.get('min_score', 0)} / max {quality_overview.get('max_score', 0)})"
            )
            global_topic = (quality_overview.get("global_news_topic") or {})
            if global_topic.get("summary"):
                lines.append(f"- Recurring news themes: {global_topic.get('summary')}")
            lines.append("")

        # Indices
        lines.append("## Indices")
        for idx in data["indices"]:
            change_pct = safe_num(idx.change_pct)
            direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
            lines.append(
                f"- {idx.name}: {safe_num(idx.current_price):.2f} "
                f"{direction} {change_pct:+.2f}% "
                f"turnover: Rs {safe_num(idx.turnover) / 1e7:.0f} Cr"
            )

        # Watchlist details
        lines.append("\n## Watchlist details")
        packs = data.get("signal_packs", {}) or {}

        for w in context.watchlist:
            pack = packs.get(w.symbol)
            stock_ctx = symbol_contexts.get(w.symbol, {}) or {}
            stock_quality = (stock_ctx.get("data_quality") or {})
            quote = pack.quote if pack else None
            stock_name = (w.name or (quote.name if quote else "") or w.symbol).strip()
            lines.append(f"\n### {stock_name} ({w.symbol})")
            if stock_quality:
                lines.append(
                    f"- Data quality: {stock_quality.get('score', 0)} (live news {stock_quality.get('realtime_news_count', 0)}, extended news {stock_quality.get('extended_news_count', 0)}, historical news {stock_quality.get('history_news_count', 0)})"
                )

            # Quote
            if quote:
                change_pct = safe_num(quote.change_pct)
                direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"

                current_price = safe_num(quote.current_price)
                high_price = safe_num(quote.high_price)
                low_price = safe_num(quote.low_price)
                prev_close = safe_num(quote.prev_close, 1)  # avoid division by zero
                turnover = safe_num(quote.turnover)

                lines.append(
                    f"- Today: {current_price:.2f} {direction} {change_pct:+.2f}%"
                )
                amplitude = (
                    (high_price - low_price) / prev_close * 100 if prev_close > 0 else 0
                )
                lines.append(
                    f"- Range: {amplitude:.1f}%  high {high_price:.2f} low {low_price:.2f}"
                )
                lines.append(f"- Turnover: Rs {turnover / 1e7:.2f} Cr")
            else:
                current_price = 0
                lines.append("- Today: no quote data")

            # Technicals
            tech = (pack.technical if pack else None) or {"error": "No technical data"}
            if not tech.get("error"):
                ma5 = safe_num(tech.get("ma5"))
                ma10 = safe_num(tech.get("ma10"))
                ma20 = safe_num(tech.get("ma20"))
                lines.append(f"- Moving averages: MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}")
                lines.append(
                    f"- Trend: {tech.get('trend', 'unknown')}, MACD {tech.get('macd_status', 'unknown')}"
                )
                change_5d = tech.get("change_5d")
                change_20d = tech.get("change_20d")
                if change_5d is not None:
                    lines.append(
                        f"- Recent: 5d {change_5d:+.1f}% 20d {safe_num(change_20d):+.1f}%"
                    )
                if tech.get("volume_trend"):
                    vol_ratio = tech.get("volume_ratio")
                    ratio_str = (
                        f" (volume ratio {vol_ratio:.2f})" if vol_ratio is not None else ""
                    )
                    lines.append(f"- Volume: {tech.get('volume_trend')}{ratio_str}")
                if tech.get("rsi6") is not None and tech.get("rsi_status"):
                    lines.append(
                        f"- RSI: {tech.get('rsi6'):.1f} ({tech.get('rsi_status')})"
                    )
                if tech.get("kdj_status"):
                    kdj_k = tech.get("kdj_k")
                    kdj_d = tech.get("kdj_d")
                    kdj_j = tech.get("kdj_j")
                    if kdj_k is not None and kdj_d is not None and kdj_j is not None:
                        lines.append(
                            f"- KDJ: {tech.get('kdj_status')} (K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f})"
                        )
                    else:
                        lines.append(f"- KDJ: {tech.get('kdj_status')}")
                if tech.get("boll_status"):
                    boll_upper = tech.get("boll_upper")
                    boll_lower = tech.get("boll_lower")
                    if boll_upper is not None and boll_lower is not None:
                        lines.append(
                            f"- Bollinger: {tech.get('boll_status')} (upper {boll_upper:.2f} lower {boll_lower:.2f})"
                        )
                    else:
                        lines.append(f"- Bollinger: {tech.get('boll_status')}")
                if tech.get("kline_pattern"):
                    lines.append(f"- Pattern: {tech.get('kline_pattern')}")
                if tech.get("amplitude") is not None:
                    amp = tech.get("amplitude")
                    amp5 = tech.get("amplitude_avg5")
                    if amp5 is not None:
                        lines.append(f"- Range: {amp:.1f}% (5-day average {amp5:.1f}%)")
                    else:
                        lines.append(f"- Range: {amp:.1f}%")
                support_m = tech.get("support_m")
                resistance_m = tech.get("resistance_m")
                if support_m is not None and resistance_m is not None:
                    lines.append(
                        f"- Support/resistance: medium-term support {support_m:.2f} resistance {resistance_m:.2f}"
                    )
                else:
                    support = tech.get("support")
                    resistance = tech.get("resistance")
                    if support is not None and resistance is not None:
                        lines.append(
                            f"- Support/resistance: support {support:.2f} resistance {resistance:.2f}"
                        )

            # Fund flows (only when a source provides them)
            flow = (pack.capital_flow if pack else None) or {}
            if not flow.get("error") and flow.get("status"):
                inflow = safe_num(flow.get("main_net_inflow"))
                inflow_pct = safe_num(flow.get("main_net_inflow_pct"))
                inflow_str = (
                    f"Rs {inflow / 1e7:+.2f} Cr"
                    if abs(inflow) >= 1e8
                    else f"Rs {inflow / 1e5:+.2f} L"
                )
                lines.append(
                    f"- Flows: {flow['status']}, large-order net inflow {inflow_str} ({inflow_pct:+.1f}%)"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "no data":
                    lines.append(f"- 5-day flows: {flow['trend_5d']}")

            # News and announcements
            stock_news = (
                (stock_ctx.get("news") or {}).get("realtime")
                or (stock_ctx.get("news") or {}).get("extended")
                or (pack.news.items if (pack and pack.news) else [])
            )
            if stock_news:
                lines.append("- Related news:")
                for n in stock_news[:3]:
                    source_label = n.get("source")
                    importance_star = (
                        "⭐" * (n.get("importance") or 0) if n.get("importance") else ""
                    )
                    time_str = n.get("time") or ""
                    title = n.get("title") or ""
                    link = f"[source]({n.get('url')})" if n.get("url") else ""
                    lines.append(
                        f"  - [{time_str}] {importance_star}{title} ({source_label}){(' ' + link) if link else ''}"
                    )
            else:
                lines.append("- Related news: none")
            history_topic = ((stock_ctx.get("news") or {}).get("history_topic") or {})
            if history_topic.get("summary"):
                lines.append(f"- Remembered news (30 days): {history_topic.get('summary')}")

            # Events (last N days, from structured announcements)
            events = pack.events.items if (pack and pack.events) else []
            important_events = [e for e in events if (e.get("importance") or 0) >= 2]
            if important_events:
                lines.append("- Events:")
                for e in important_events[:2]:
                    time_str = e.get("time") or ""
                    et = e.get("event_type") or "notice"
                    title = e.get("title") or ""
                    link = f"[source]({e.get('url')})" if e.get("url") else ""
                    lines.append(
                        f"  - [{time_str}] ({et}) {title}{(' ' + link) if link else ''}"
                    )

            # Position
            position = None
            if pack and pack.position and pack.position.aggregated:
                position = pack.position.aggregated
            else:
                try:
                    position = context.portfolio.get_aggregated_position(w.symbol)
                except Exception:
                    position = None

            if position:
                total_qty = position.get("total_quantity")
                avg_cost = safe_num(position.get("avg_cost"), 1)
                pnl_pct = (
                    (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
                )
                style_labels = {"short": "Short term", "swing": "Swing", "long": "Long term"}
                style = style_labels.get(position.get("trading_style", "swing"), "Swing")
                if total_qty is not None:
                    lines.append(
                        f"- Position: {total_qty} shares at cost {avg_cost:.2f}, unrealised {pnl_pct:+.1f}% ({style})"
                    )

            kline_history = stock_ctx.get("kline_history") or {}
            if kline_history.get("available"):
                ret_5d = kline_history.get("ret_5d")
                ret_20d = kline_history.get("ret_20d")
                ret_60d = kline_history.get("ret_60d")
                lines.append(
                    "- Returns: "
                    f"5d {(f'{ret_5d:+.1f}%' if ret_5d is not None else 'N/A')} "
                    f"20d {(f'{ret_20d:+.1f}%' if ret_20d is not None else 'N/A')} "
                    f"60d {(f'{ret_60d:+.1f}%' if ret_60d is not None else 'N/A')}"
                )

            constraints = stock_ctx.get("constraints") or {}
            if constraints:
                lines.append(
                    f"- Cash: Rs {safe_num(constraints.get('total_available_funds'), 0):.0f} available; single-stock share {safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}% ({constraints.get('risk_budget_hint', 'normal')})"
                )
            memory = stock_ctx.get("memory") or {}
            if memory:
                lines.append(
                    f"- Context memory: average quality {safe_num(memory.get('avg_quality_score'), 0):.1f} over {memory.get('window_days', 30)} days, trend {memory.get('quality_trend', 'flat')}"
                )
                if memory.get("latest_history_topic"):
                    lines.append(f"- Remembered themes: {memory.get('latest_history_topic')}")

        # Accounts
        if context.portfolio.accounts:
            lines.append("\n## Accounts")
            for acc in context.portfolio.accounts:
                if acc.positions or acc.available_funds > 0:
                    acc_cost = acc.total_cost
                    lines.append(
                        f"- {acc.name}: cost of holdings Rs {acc_cost:.0f}, cash available Rs {acc.available_funds:.0f}"
                    )
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(
                    f"- Total: cost of holdings Rs {total_cost:.0f}, cash available Rs {total_funds:.0f}"
                )

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestions(self, content: str, watchlist: list) -> dict[str, dict]:
        """
        Parse per-stock actions from the AI response (recommendation mode only)
        Returns: {symbol: {action, action_label, reason, should_alert}}
        """
        suggestions: dict[str, dict] = {}
        if not content or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        name_map: dict[str, str] = {}

        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(DAILY_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Quick filter: the line must contain an action label
            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) A symbol in [...]
            m = re.search(r"\[\s*(?P<sym>[A-Za-z0-9&\-]{1,20})\s*\]", line)
            sym_raw = m.group("sym") if m else ""

            # 2) A symbol in parentheses, e.g. Infosys (INFY)
            if not sym_raw:
                m = re.search(r"\(\s*(?P<sym>[A-Za-z0-9&\-]{1,20})\s*\)", line)
                sym_raw = m.group("sym") if m else ""

            # 3) A symbol at the start of the line, e.g. INFY Keep holding: ...
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z0-9&\-]{1,20})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) Fall back to containment (the AI may add prefixes or suffixes)
            if not sym_raw:
                for k in sorted(symbol_map.keys(), key=len, reverse=True):
                    if k and k in line.upper():
                        sym_raw = k
                        break

            # 5) Fall back to the company name
            if not sym_raw:
                for name, sym in name_map.items():
                    if name and name in line:
                        sym_raw = sym
                        break

            if not sym_raw:
                continue

            sym_key = sym_raw.strip()
            canonical = symbol_map.get(sym_key.upper()) or symbol_map.get(sym_key)
            if not canonical and sym_key.isdigit():
                canonical = symbol_map.get(sym_key)

            if not canonical or canonical not in symbol_set:
                continue

            # The reason is the text after the action label
            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[:\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = DAILY_ACTION_MAP.get(
                action_text, {"action": "hold", "label": "Keep holding"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:100],
                "should_alert": action_info["action"] in ["add", "reduce", "sell"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        """Parse suggestions from structured JSON block."""
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym

        for it in items:
            if not isinstance(it, dict):
                continue
            sym_raw = (it.get("symbol") or "").strip()
            if not sym_raw:
                continue
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "hold").strip()
            action_label = (it.get("action_label") or "Keep holding").strip()
            reason = (it.get("reason") or "").strip()
            signal = (it.get("signal") or "").strip()

            suggestions[canonical] = {
                "action": action,
                "action_label": action_label,
                "reason": reason[:160],
                "signal": signal[:60],
                "triggers": it.get("triggers")
                if isinstance(it.get("triggers"), list)
                else [],
                "invalidations": it.get("invalidations")
                if isinstance(it.get("invalidations"), list)
                else [],
                "risks": it.get("risks") if isinstance(it.get("risks"), list) else [],
                "should_alert": action in ["add", "reduce", "sell"],
            }

        return suggestions

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """Run the AI analysis and save it to history and the suggestion pool."""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        # Keep structured JSON block at the very end.
        if context.model_label:
            idx = content.rfind(TAG_START)
            if idx >= 0:
                content = (
                    content[:idx].rstrip()
                    + f"\n\n---\nAI: {context.model_label}\n\n"
                    + content[idx:]
                )
            else:
                content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        structured = try_extract_tagged_json(content) or {}
        display_content = strip_tagged_json(content)

        stock_items = [
            f"{(s.name or s.symbol).strip()}({s.symbol})"
            for s in context.watchlist[:5]
        ]
        stock_names = ", ".join(stock_items) if stock_items else "no stocks"
        if len(context.watchlist) > 5:
            stock_names += f" and {len(context.watchlist) - 5} more"
        title = f"[{self.display_name}] {stock_names}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # Per-stock actions
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
            suggestions = self._parse_suggestions(result.content, context.watchlist)
        if not is_feature_enabled(Feature.SUGGESTION_POOL):
            # Research-only: no per-security actions are produced or stored (ADR-004).
            suggestions = {}
        result.raw_data["suggestions"] = suggestions
        research_items = parse_research_items(
            structured, allowed_symbols=[s.symbol for s in context.watchlist]
        )
        result.raw_data["research"] = research_payload(research_items)

        # Save each stock's item to the suggestion pool
        stock_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        symbol_contexts = data.get("symbol_contexts", {}) or {}
        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if stock:
                pack = packs.get(symbol)
                trigger_price = (
                    getattr(pack.quote, "current_price", None)
                    if pack and pack.quote
                    else None
                )
                quality_score = (
                    (symbol_contexts.get(symbol, {}) or {})
                    .get("data_quality", {})
                    .get("score")
                )
                save_suggestion(
                    stock_symbol=symbol,
                    stock_name=stock.name,
                    action=sug["action"],
                    action_label=sug["action_label"],
                    signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                    reason=sug.get("reason", ""),
                    agent_name=self.name,
                    agent_label=self.display_name,
                    expires_hours=16,  # post-market items last overnight
                    prompt_context=user_content,
                    ai_response=result.content,
                    stock_market=stock.market.value,
                    meta={
                        "analysis_date": analysis_date,
                        "source": "daily_report",
                        "context_quality_score": quality_score,
                        "plan": {
                            "triggers": sug.get("triggers")
                            if isinstance(sug.get("triggers"), list)
                            else [],
                            "invalidations": sug.get("invalidations")
                            if isinstance(sug.get("invalidations"), list)
                            else [],
                            "risks": sug.get("risks")
                            if isinstance(sug.get("risks"), list)
                            else [],
                        }
                        if isinstance(sug, dict)
                        else {},
                    },
                )
                prediction_group_id = str(uuid.uuid4())
                for horizon in (1, 5):
                    save_agent_prediction_outcome(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        stock_market=stock.market.value,
                        prediction_date=analysis_date,
                        horizon_days=horizon,
                        prediction_group_id=prediction_group_id,
                        action=sug.get("action") or "hold",
                        action_label=sug.get("action_label") or "Keep holding",
                        confidence=(float(quality_score) / 100.0)
                        if quality_score is not None
                        else None,
                        trigger_price=trigger_price,
                        meta={
                            "source": "daily_report",
                            "reason": sug.get("reason", ""),
                            "signal": sug.get("signal", ""),
                        },
                    )

        # Save to history ("*" marks a market-wide analysis)
        # Keep only the key fields in raw_data
        symbols = [s.symbol for s in context.watchlist]
        compact_context = {}
        context_payload = {}
        for sym, ctx in symbol_contexts.items():
            layered_news = ctx.get("news") or {}
            events = ctx.get("events") or []
            compact_context[sym] = {
                "data_quality": ctx.get("data_quality") or {},
                "history_news_topic": ((ctx.get("news") or {}).get("history_topic"))
                or {},
                "kline_history": ctx.get("kline_history") or {},
                "constraints": ctx.get("constraints") or {},
                "memory": ctx.get("memory") or {},
            }
            context_payload[sym] = {
                "data_quality": ctx.get("data_quality") or {},
                "kline_history": ctx.get("kline_history") or {},
                "constraints": ctx.get("constraints") or {},
                "memory": ctx.get("memory") or {},
                "news": {
                    "realtime": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("realtime") or [])[:3]
                    ],
                    "extended": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("extended") or [])[:3]
                    ],
                    "history": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("history") or [])[:3]
                    ],
                    "history_topic": layered_news.get("history_topic") or {},
                },
                "events": [
                    {
                        "time": e.get("time"),
                        "title": e.get("title"),
                        "event_type": e.get("event_type"),
                        "importance": e.get("importance"),
                    }
                    for e in events[:3]
                ],
            }
        quality_overview = data.get("quality_overview") or {}
        news_debug = {}
        for sym, ctx in symbol_contexts.items():
            layered = ctx.get("news") or {}
            news_debug[sym] = {
                "realtime_count": len(layered.get("realtime") or []),
                "extended_count": len(layered.get("extended") or []),
                "history_count": len(layered.get("history") or []),
            }
        save_agent_context_run(
            agent_name=self.name,
            stock_symbol="*",
            analysis_date=analysis_date,
            context_payload={
                "quality_overview": quality_overview,
                "symbols": compact_context,
            },
            quality={"score": quality_overview.get("avg_score", 0)},
        )
        history_saved = save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "symbols": symbols,
                "timestamp": data.get("timestamp"),
                "quality_overview": quality_overview,
                "context_summary": compact_context,
                "context_payload": context_payload,
                "prompt_context": user_content[:12000],
                "prompt_stats": {
                    "prompt_chars": len(user_content or ""),
                    "watchlist_count": len(context.watchlist),
                },
                "news_debug": news_debug,
                "suggestions": suggestions,
                "research": result.raw_data.get("research", []),
            },
        )
        if history_saved:
            logger.info(f"Daily close report saved to history with {len(suggestions)} items")
        else:
            logger.error("Saving the daily close report to history failed")

        return result
