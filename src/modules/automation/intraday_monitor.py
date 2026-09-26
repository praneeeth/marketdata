"""Intraday monitor agent: watches holdings during the session; the AI decides whether an alert is worth sending."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.modules.automation.research_output import (
    load_research_prompt,
    parse_intraday_observation,
    render_intraday_observation,
)
from src.platform.compliance import Feature, is_feature_enabled
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.research.analysis_history import get_latest_analysis, get_analysis
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import try_parse_action_json
from src.platform.marketdata.models import MarketCode, StockData, MARKETS

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """Whether the stock's market is in session."""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    return "India (NSE/BSE)" if market == MarketCode.IN else market.value


# Normalised action labels (recommendation mode only; unreachable in research_only)
SUGGESTION_TYPES = {
    "Open position": "buy",  # new position
    "Add": "add",  # increase an existing position
    "Reduce": "reduce",  # decrease a position
    "Exit": "sell",  # close the whole position
    "Hold": "hold",  # keep as is
    "Watch": "watch",  # no action for now
}

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "intraday_monitor.txt"


class IntradayMonitorAgent(BaseAgent):
    """
    Intraday monitor agent

    Features:
    - Single mode: analyses one stock at a time and notifies per stock
    - AI judgement: the stock data goes to the AI, which decides whether an alert is worthwhile
    - Throttling: the same stock is not notified again within a short window
    - Technicals: includes K-lines and technical indicators
    """

    name = "intraday_monitor"
    display_name = "Intraday monitor"
    description = "Watches holdings during market hours; the AI flags notable signals"

    def __init__(
        self,
        throttle_minutes: int = 30,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        event_only: bool = True,
        price_alert_threshold: float = 3.0,
        volume_alert_ratio: float = 2.0,
        stop_loss_warning: float = -5.0,
        take_profit_warning: float = 10.0,
    ):
        """
        Args:
            throttle_minutes: minimum minutes between notifications for the same stock
            bypass_throttle: skip throttling (for tests)
            bypass_market_hours: skip the market-hours gate (manual analysis only)
            price_alert_threshold: % move beyond which the price move counts as unusual
            volume_alert_ratio: volume ratio beyond which volume counts as unusual
            stop_loss_warning: unrealised loss % that triggers the user's loss alert
            take_profit_warning: unrealised gain % that triggers the user's gain alert
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.event_only = event_only
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.stop_loss_warning = stop_loss_warning
        self.take_profit_warning = take_profit_warning

    async def collect(self, context: AgentContext) -> dict:
        """Collect the live quote, K-lines and earlier analyses."""
        if not context.watchlist:
            logger.warning("Watchlist is empty; skipping the intraday monitor")
            return {"stocks": [], "stock_data": None}

        # SignalPack: one structured input (quote/technical/position)
        stock_config = context.watchlist[0] if context.watchlist else None
        market = stock_config.market if stock_config else MarketCode.IN
        symbol = stock_config.symbol if stock_config else ""
        name = stock_config.name if stock_config else symbol

        # Gate on the stock's own market hours
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = f"{market_label(market)} is closed; skipped"
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=True,
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

        # Earlier analyses give the AI more context
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the intraday analysis prompt."""
        system_prompt = load_research_prompt("intraday_monitor.txt")

        # Helper: safe numeric access; None becomes the default
        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "No stock data"

        # Positions across all accounts
        positions = context.portfolio.get_positions_for_stock(stock.symbol)
        style_labels = {"short": "Short term", "swing": "Swing", "long": "Long term"}

        lines = []
        lines.append(f"## Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # Quote
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        change_amount = safe_num(stock.change_amount)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## Quote")
        lines.append(f"- Stock: {stock.name} ({stock.symbol})")
        lines.append(f"- Last price: {current_price:.2f}")
        lines.append(f"- Change: {change_pct:+.2f}%")
        lines.append(f"- Change (Rs): {change_amount:+.2f}")
        lines.append(f"- Open: {open_price:.2f}")
        lines.append(f"- High: {high_price:.2f}")
        lines.append(f"- Low: {low_price:.2f}")
        lines.append(f"- Previous close: {prev_close:.2f}")
        if volume > 0:
            lines.append(f"- Volume: {volume:.0f} shares")
        if turnover > 0:
            lines.append(f"- Turnover: Rs {turnover / 1e7:.2f} Cr")

        # System thresholds (help the AI make a steadier alert / no-alert call)
        # Unusual price moves use an adaptive threshold relative to the stock's own volatility (ATR%); the fixed threshold is the floor.
        from src.modules.strategy.intraday_event_gate import (
            DEFAULT_ATR_K,
            adaptive_price_threshold,
            is_abnormal_move,
        )

        kline_for_atr = data.get("kline_summary") or {}
        atr_pct = kline_for_atr.get("atr_pct")
        adaptive_threshold = adaptive_price_threshold(
            atr_pct, self.price_alert_threshold, DEFAULT_ATR_K
        )

        lines.append("\n## System thresholds")
        if atr_pct is not None and atr_pct > 0:
            lines.append(
                f"- Unusual price move: |change| >= max(fixed threshold {self.price_alert_threshold:.1f}%, "
                f"{DEFAULT_ATR_K:g}×ATR%={atr_pct:.2f}%)={adaptive_threshold:.2f}%"
                f" (adapts to the stock's own volatility; the fixed threshold is the floor)"
            )
        else:
            lines.append(
                f"- Unusual price move: |change| >= {self.price_alert_threshold:.1f}%"
                f" (ATR unavailable; using the fixed threshold)"
            )
        lines.append(f"- Unusual volume: volume ratio >= {self.volume_alert_ratio:.1f}")
        lines.append(f"- Loss alert threshold set by the user: P&L <= {self.stop_loss_warning:.1f}%")
        lines.append(f"- Gain alert threshold set by the user: P&L >= {self.take_profit_warning:.1f}%")
        price_hit = (
            "triggered"
            if is_abnormal_move(
                change_pct,
                atr_pct,
                k=DEFAULT_ATR_K,
                fixed_threshold=self.price_alert_threshold,
            )
            else "not triggered"
        )
        lines.append(f"- Current change: {change_pct:+.2f}% ({price_hit})")

        symbol_ctx = data.get("symbol_context") or {}
        quality = (symbol_ctx.get("data_quality") or {})
        if quality:
            lines.append(
                f"- Context quality: {quality.get('score', 0)} (live news {quality.get('realtime_news_count', 0)}, extended news {quality.get('extended_news_count', 0)}, historical news {quality.get('history_news_count', 0)})"
            )

        layered_news = symbol_ctx.get("news") or {}
        realtime_news = layered_news.get("realtime") or []
        extended_news = layered_news.get("extended") or []
        history_news = layered_news.get("history") or []
        if realtime_news or extended_news or history_news:
            lines.append("\n## News and events")
            chosen = realtime_news or extended_news or history_news
            for item in chosen[:3]:
                lines.append(
                    f"- [{item.get('time')}] {item.get('title')} ({item.get('source')})"
                )
            hist_topic = (layered_news.get("history_topic") or {}).get("summary")
            if hist_topic:
                lines.append(f"- Recurring news themes: {hist_topic}")

        kline_history = symbol_ctx.get("kline_history") or {}
        if kline_history.get("available"):
            lines.append("\n## Price history")
            lines.append(
                f"- Returns: 5d {format_num(kline_history.get('ret_5d'), 1)}% / 20d {format_num(kline_history.get('ret_20d'), 1)}% / 60d {format_num(kline_history.get('ret_60d'), 1)}%"
            )
            if kline_history.get("volatility_20d") is not None:
                lines.append(
                    f"- Volatility (20d std dev): {format_num(kline_history.get('volatility_20d'), 2)}%"
                )
            if kline_history.get("breakout_state") and kline_history.get("breakout_state") != "none":
                lines.append(f"- Breakout state: {kline_history.get('breakout_state')}")

        # K-lines and technical indicators
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## Technicals")

            # Trend
            lines.append(f"- Trend: {kline.get('trend', 'N/A')}")
            lines.append(
                f"- Last 5 days: {kline.get('recent_5_up', 0)} up, {5 - kline.get('recent_5_up', 0)} down"
            )
            lines.append(
                f"- 5d change: {format_num(kline.get('change_5d'))}% | 20d change: {format_num(kline.get('change_20d'))}%"
            )

            # MACD
            macd_info = f"MACD: {kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f" ({kline.get('macd_cross_days')} days ago)"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get("rsi_status")
            rsi6 = kline.get("rsi6")
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6): {rsi6:.1f} ({rsi_status})")

            # KDJ
            kdj_status = kline.get("kdj_status")
            kdj_k, kdj_d, kdj_j = (
                kline.get("kdj_k"),
                kline.get("kdj_d"),
                kline.get("kdj_j"),
            )
            if kdj_status and kdj_k is not None:
                lines.append(
                    f"- KDJ: K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f} ({kdj_status})"
                )

            # Bollinger bands
            boll_status = kline.get("boll_status")
            boll_upper, boll_lower = kline.get("boll_upper"), kline.get("boll_lower")
            if boll_status and boll_upper is not None:
                lines.append(
                    f"- Bollinger: upper={format_num(boll_upper)} lower={format_num(boll_lower)} ({boll_status})"
                )

            # Volume
            volume_trend = kline.get("volume_trend")
            volume_ratio = kline.get("volume_ratio")
            if volume_trend:
                vol_info = f"Volume: {volume_trend}"
                if volume_ratio:
                    vol_info += f" (volume ratio={volume_ratio:.2f})"
                lines.append(f"- {vol_info}")
                if volume_ratio:
                    vol_hit = (
                        "triggered" if volume_ratio >= self.volume_alert_ratio else "not triggered"
                    )
                    lines.append(f"- Volume ratio threshold: {vol_hit}")

            # Volatility (ATR): the stock's own baseline, to tell unusual moves from normal ones
            atr_val = kline.get("atr")
            atr_pct_val = kline.get("atr_pct")
            if atr_pct_val is not None:
                atr_line = f"Volatility: ATR={format_num(atr_val)} (ATR%={format_num(atr_pct_val)}%)"
                atr_line += (
                    f"; today's change {change_pct:+.2f}% "
                    + (
                        "exceeds"
                        if abs(change_pct) >= adaptive_threshold
                        else "is within"
                    )
                    + f" the adaptive threshold of {adaptive_threshold:.2f}%"
                )
                lines.append(f"- {atr_line}")

            # Moving averages
            lines.append(
                f"- MA5: {format_num(kline.get('ma5'))} | MA10: {format_num(kline.get('ma10'))} | MA20: {format_num(kline.get('ma20'))} | MA60: {format_num(kline.get('ma60'))}"
            )

        # Fund flows (only when a source provides them)
        pack = data.get("signal_pack")
        flow = getattr(pack, "capital_flow", None) if pack else None
        if (
            isinstance(flow, dict)
            and flow
            and not flow.get("error")
            and flow.get("status")
        ):
            try:
                inflow = float(flow.get("main_net_inflow") or 0)
                inflow_pct = float(flow.get("main_net_inflow_pct") or 0)
                inflow_str = (
                    f"Rs {inflow / 1e7:+.2f} Cr"
                    if abs(inflow) >= 1e8
                    else f"Rs {inflow / 1e5:+.2f} L"
                )
                lines.append("\n## Fund flows")
                lines.append(
                    f"- Flows: {flow.get('status')}, large-order net inflow {inflow_str} ({inflow_pct:+.1f}%)"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "no data":
                    lines.append(f"- 5-day flows: {flow.get('trend_5d')}")
            except Exception:
                pass

            # Support and resistance levels
            support_m, resistance_m = kline.get("support_m"), kline.get("resistance_m")
            if support_m and resistance_m:
                lines.append(
                    f"- Medium-term support: {format_num(support_m)} | resistance: {format_num(resistance_m)}"
                )

            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- Short-term support: {format_num(support_s)} | resistance: {format_num(resistance_s)}"
                )

            # Candlestick pattern
            kline_pattern = kline.get("kline_pattern")
            if kline_pattern:
                lines.append(f"- Candlestick pattern: {kline_pattern}")

            # Range
            amplitude = kline.get("amplitude")
            amplitude_avg5 = kline.get("amplitude_avg5")
            if amplitude is not None:
                amp_info = f"Today's range: {amplitude:.2f}%"
                if amplitude_avg5 is not None:
                    amp_info += f" (5-day average: {amplitude_avg5:.2f}%)"
                lines.append(f"- {amp_info}")

        # Account cash
        lines.append("\n## Account cash")
        lines.append(f"- Total available cash: Rs {context.portfolio.total_available_funds:.0f}")
        for acc in context.portfolio.accounts:
            lines.append(f"  - {acc.name}: Rs {acc.available_funds:.0f}")
        constraints = symbol_ctx.get("constraints") or {}
        if constraints:
            lines.append(
                f"- Single-stock share of portfolio: {safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}% ({constraints.get('risk_budget_hint', 'normal')})"
            )
        memory = symbol_ctx.get("memory") or {}
        if memory:
            lines.append(
                f"- Context memory: average quality {safe_num(memory.get('avg_quality_score'), 0):.1f} over {memory.get('window_days', 30)} days, trend {memory.get('quality_trend', 'flat')}"
            )
            if memory.get("latest_history_topic"):
                lines.append(f"- Remembered themes: {memory.get('latest_history_topic')}")

        # Positions per account
        if positions:
            lines.append(f"\n## Positions ({len(positions)} accounts)")
            for i, pos in enumerate(positions, 1):
                cost_price = safe_num(pos.cost_price, 1)
                pnl_pct = (
                    (current_price - cost_price) / cost_price * 100
                    if cost_price > 0
                    else 0
                )
                style_label = style_labels.get(pos.trading_style, "Swing")
                market_value = current_price * pos.quantity
                # Available cash of the matching account
                acc_funds = 0
                for acc in context.portfolio.accounts:
                    if acc.id == pos.account_id:
                        acc_funds = acc.available_funds
                        break

                lines.append(f"\n### Position {i}: {pos.account_name}")
                lines.append(f"- Style: {style_label}")
                lines.append(f"- Cost price: {cost_price:.2f}")
                lines.append(f"- Quantity: {pos.quantity} shares")
                lines.append(f"- Market value: Rs {market_value:.0f}")
                pnl_note = ""
                if pnl_pct <= self.stop_loss_warning:
                    pnl_note = " (at or below the user's loss alert threshold)"
                elif pnl_pct >= self.take_profit_warning:
                    pnl_note = " (at or above the user's gain alert threshold)"
                lines.append(f"- Unrealised P&L: {pnl_pct:+.1f}%{pnl_note}")
                lines.append(f"- Account cash available: Rs {acc_funds:.0f}")
        else:
            lines.append("\n## Not held (watchlist only)")
            lines.append("- Not currently held")

        # Earlier analyses (help the AI judge better)
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")

        if daily_analysis or premarket_analysis:
            lines.append("\n## Earlier analyses")

            if daily_analysis:
                # Take the part about this stock (at most 300 characters)
                content = (
                    daily_analysis[:300] + "..."
                    if len(daily_analysis) > 300
                    else daily_analysis
                )
                lines.append("\n### Yesterday's post-market summary")
                lines.append(content)

            if premarket_analysis:
                content = (
                    premarket_analysis[:300] + "..."
                    if len(premarket_analysis) > 300
                    else premarket_analysis
                )
                lines.append("\n### Today's pre-market summary")
                lines.append(content)

        lines.append("\nSummarise what is notable, using the technicals, fund flows and earlier analyses.")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestion(self, content: str) -> dict:
        """
        Parse the action from the AI response (recommendation mode only)

        Returns:
            {
                "action": "hold",  # buy/add/reduce/sell/hold/watch
                "action_label": "Hold",
                "signal": "...",
                "reason": "...",
                "should_alert": True
            }
        """
        result = {
            "action": "watch",
            "action_label": "Watch",
            "signal": "",
            "reason": "",
            "should_alert": False,
        }

        # 1) Prefer JSON output (structured mode)
        obj = try_parse_action_json(content) or self._try_parse_loose_json(content)
        if obj:
            action = (obj.get("action") or "watch").strip()
            result["action"] = action
            result["action_label"] = (
                obj.get("action_label") or result["action_label"]
            ).strip()[:20]
            result["signal"] = (obj.get("signal") or "").strip()[:60]
            result["reason"] = (obj.get("reason") or "").strip()[:160]
            result["should_alert"] = action in {
                "buy",
                "add",
                "reduce",
                "sell",
                "alert",
                "avoid",
            }
            result["triggers"] = (
                obj.get("triggers") if isinstance(obj.get("triggers"), list) else []
            )
            result["invalidations"] = (
                obj.get("invalidations")
                if isinstance(obj.get("invalidations"), list)
                else []
            )
            result["risks"] = (
                obj.get("risks") if isinstance(obj.get("risks"), list) else []
            )
            return result

        # No alert needed?
        if "[NO_ALERT]" in content:
            result["should_alert"] = False
            result["action"] = "hold"
            result["action_label"] = "Hold"
            return result

        # Action type (searched across the whole text)
        for label, action in SUGGESTION_TYPES.items():
            if label in content:
                result["action"] = action
                result["action_label"] = label
                break

        # Signal (several formats accepted)
        signal_patterns = [
            r"\*\*Signal\*\*\s*:?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Signal\s*:\s*(.+?)(?=\n|$)",
        ]
        for pattern in signal_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["signal"] = match.group(1).strip()[:50]
                break

        # Suggestion text (several formats accepted)
        suggest_patterns = [
            r"\*\*Suggestion\*\*\s*:?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Suggestion\s*:\s*(.+?)(?=\n|$)",
        ]
        for pattern in suggest_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                suggest_text = match.group(1).strip()
                # Action type from the suggestion text
                for label, action in SUGGESTION_TYPES.items():
                    if label in suggest_text:
                        result["action"] = action
                        result["action_label"] = label
                        break
                # Use the suggestion as the signal when there is none
                if not result["signal"]:
                    result["signal"] = suggest_text[:50]
                break

        # Reason (several formats accepted)
        reason_patterns = [
            r"\*\*Reason\*\*\s*:?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Reason\s*:\s*(.+?)(?=\n|$)",
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["reason"] = match.group(1).strip()[:100]
                break

        # With no signal or reason, fall back to the start of the text
        if not result["signal"] and not result["reason"]:
            # First 100 characters after stripping markdown
            clean_content = re.sub(r"\*\*|##|#", "", content).strip()
            # Skip the no-alert case
            if not clean_content.startswith("[NO_ALERT]"):
                result["reason"] = clean_content[:100]

        # should_alert only for an explicit open/add/reduce/exit action
        result["should_alert"] = result["action"] in {"buy", "add", "reduce", "sell"}
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """Lenient JSON parsing that tolerates malformed model output."""
        raw = (text or "").strip()
        if not raw:
            return None

        # Tolerate a leading "json" line
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()

        # Strip a fenced code block
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()

        # Parse directly; otherwise extract the first JSON object
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if not isinstance(obj, dict):
            return None

        # Without the key fields this is not a suggestion JSON
        keys = {"action", "action_label", "signal", "reason", "triggers", "invalidations", "risks"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, raw_content: str
    ) -> str:
        """Readable notification text when the model returns JSON."""
        action_label = suggestion.get("action_label") or "Watch"
        signal = suggestion.get("signal") or "No clear new signal"
        reason = suggestion.get("reason") or "No further detail."
        triggers = (
            suggestion.get("triggers")
            if isinstance(suggestion.get("triggers"), list)
            else []
        )
        invalidations = (
            suggestion.get("invalidations")
            if isinstance(suggestion.get("invalidations"), list)
            else []
        )
        risks = (
            suggestion.get("risks") if isinstance(suggestion.get("risks"), list) else []
        )
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        lines = [
            f"{stock.name} ({stock.symbol})",
            f"Last: {price}  Change: {chg}",
            f"Action: {action_label}",
            f"Signal: {signal}",
            f"Reason: {reason}",
        ]
        if triggers:
            lines.append("Trigger conditions:")
            lines.extend([f"- {str(x)}" for x in triggers[:3]])
        if invalidations:
            lines.append("Invalidation conditions:")
            lines.extend([f"- {str(x)}" for x in invalidations[:3]])
        if risks:
            lines.append("Risks:")
            lines.extend([f"- {str(x)}" for x in risks[:3]])
        # If the output was not pure JSON, add a short excerpt for checking
        if not (try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content)):
            brief = re.sub(r"\s+", " ", (raw_content or "").strip())[:200]
            if brief:
                lines.append(f"Note: {brief}")
        return "\n".join(lines)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI analysis and the alert decision."""
        # Skip outside market hours
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"[{self.display_name}] Skipped",
                content=data.get("skip_reason", "Skipped"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")

        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"[{self.display_name}] No data",
                content="No stock data was available",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)

        # Log the full prompt for debugging
        logger.info(f"=== Prompt for {stock.symbol} ===\n{user_content}")

        raw_content = await context.ai_client.chat(system_prompt, user_content)

        # Log the AI response
        logger.info(f"=== AI Response for {stock.symbol} ===\n{raw_content}")

        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        quality_score = (
            (data.get("symbol_context") or {}).get("data_quality", {}).get("score")
        )
        if is_feature_enabled(Feature.SUGGESTION_POOL):
            # Parse the action (recommendation mode only; unreachable while research-only)
            suggestion = self._parse_suggestion(raw_content)
            content = raw_content
            # Turn JSON-like output into readable text so channels never push raw JSON
            if try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content):
                content = self._format_human_readable_content(stock, suggestion, raw_content)
        else:
            # Research-only: a factual "is something notable happening" observation.
            observation = parse_intraday_observation(raw_content)
            suggestion = {
                "action": "watch",
                "action_label": "",
                "signal": observation.headline,
                "reason": "",
                "should_alert": observation.notable,
                "triggers": [],
                "invalidations": [],
                "risks": list(observation.key_risks),
            }
            content = render_intraday_observation(
                name=stock.name,
                symbol=stock.symbol,
                price=stock.current_price,
                change_pct=stock.change_pct,
                observation=observation,
            )

        # Save to the suggestion pool (with the prompt context)
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=6,  # intraday items stay valid for 6 hours
            prompt_context=user_content,  # keep the prompt context
            ai_response=raw_content,  # keep the raw AI response
            stock_market=stock.market.value,
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "kline_meta": {
                    "computed_at": (data.get("kline_summary") or {}).get("computed_at"),
                    "asof": (data.get("kline_summary") or {}).get("asof"),
                },
                "event_gate": data.get("event_gate"),
                "analysis_date": analysis_date,
                "context_quality_score": quality_score,
                "plan": {
                    "triggers": suggestion.get("triggers")
                    if isinstance(suggestion, dict)
                    else [],
                    "invalidations": suggestion.get("invalidations")
                    if isinstance(suggestion, dict)
                    else [],
                    "risks": suggestion.get("risks")
                    if isinstance(suggestion, dict)
                    else [],
                },
            },
        )
        prediction_group_id = str(uuid.uuid4())
        for horizon in (1, 5):
            save_agent_prediction_outcome(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                stock_market=stock.market.value,
                prediction_date=analysis_date,
                horizon_days=horizon,
                prediction_group_id=prediction_group_id,
                action=suggestion.get("action") or "watch",
                action_label=suggestion.get("action_label") or "Watch",
                confidence=(float(quality_score) / 100.0)
                if quality_score is not None
                else None,
                trigger_price=getattr(stock, "current_price", None),
                meta={
                    "source": "intraday_monitor",
                    "reason": suggestion.get("reason", ""),
                    "signal": suggestion.get("signal", ""),
                },
            )

        save_agent_context_run(
            agent_name=self.name,
            stock_symbol=stock.symbol,
            analysis_date=analysis_date,
            context_payload={
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
            },
            quality={"score": quality_score or 0},
        )

        # Title
        title = f"[{self.display_name}] {stock.name} {stock.change_pct:+.2f}%"

        # Attach the AI model information
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        # Sharp-move hook: past the threshold, trigger a TradingAgents deep analysis asynchronously (off by default)
        try:
            from src.modules.automation.tradingagents.operations import try_auto_trigger
            try_auto_trigger(stock, source_agent=self.name)
        except Exception:
            logger.exception("TradingAgents trigger failed; returning the intraday result anyway")

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Whether to send a notification."""
        # Skipped results are not notified
        if result.raw_data.get("skipped"):
            return False

        # The AI judged no alert is needed
        if not result.raw_data.get("should_alert", True):
            logger.info(
                f"AI judged no alert needed: {result.raw_data.get('stock', {}).get('symbol')}"
            )
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data:
            return False

        symbol = stock_data.get("symbol")
        if not symbol:
            return False

        # Throttling (tests may skip it)
        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(
                    f"Throttled: {symbol} was notified within {self.throttle_minutes} minutes"
                )
                return False
        else:
            logger.info(f"Throttle check skipped (test mode): {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """Whether a notification may be sent (not throttled)."""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            if not record:
                return True

            # Compare in UTC so container/deployment time zones don't matter
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """Update the throttle record."""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                # Is it a new day?
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )

            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        Single mode: analyse only the given stock

        Used for live monitoring; each stock is analysed and notified on its own
        """
        # Keep only the given stock
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("stock_data"):
                return None

            # The event gate is context only; it never blocks the AI analysis.
            # Product policy: analysis refreshes continuously; should_alert plus throttling keeps notifications quiet.
            if self.event_only:
                try:
                    from src.modules.strategy.intraday_event_gate import check_and_update

                    stock = data.get("stock_data")
                    kline_summary = data.get("kline_summary")
                    decision = check_and_update(
                        symbol=stock_symbol,
                        change_pct=getattr(stock, "change_pct", None),
                        volume_ratio=(kline_summary or {}).get("volume_ratio"),
                        kline_summary=kline_summary,
                        price_threshold=self.price_alert_threshold,
                        volume_threshold=self.volume_alert_ratio,
                    )
                    data["event_gate"] = {
                        "reasons": decision.reasons,
                        "should_analyze": bool(decision.should_analyze),
                    }
                except Exception as e:
                    logger.debug(f"Event gate error; continuing the analysis: {e}")

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(
                        f"Agent [{self.display_name}] notification sent: {stock_symbol}"
                    )
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
                else:
                    notify_error = notify_result.get("error") or "unknown error"
                    result.raw_data["notify_error"] = notify_error
                    logger.error(
                        f"Agent [{self.display_name}] notification failed: {stock_symbol} - {notify_error}"
                    )
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
