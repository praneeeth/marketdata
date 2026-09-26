"""News digest agent: a summary of news about the watchlist."""

import logging
import re
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.modules.automation.research_output import (
    load_research_prompt,
    parse_research_items,
    research_payload,
)
from src.platform.compliance import Feature, is_feature_enabled
from src.platform.marketdata.collectors.news_collector import NewsCollector, NewsItem
from src.modules.research.analysis_history import save_analysis
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "news_digest.txt"

# News digest action labels (recommendation mode only; unreachable in research_only)
NEWS_ACTION_MAP = {
    "Set alert": {"action": "alert", "label": "Set alert"},
    "Watch": {"action": "watch", "label": "Watch"},
    "Keep holding": {"action": "hold", "label": "Keep holding"},
    "Consider reducing": {"action": "reduce", "label": "Consider reducing"},
    "Avoid for now": {"action": "avoid", "label": "Avoid for now"},
}


class NewsDigestAgent(BaseAgent):
    """News digest agent."""

    name = "news_digest"
    display_name = "News digest"
    description = "Fetches news about your holdings on a schedule and sends a summary"

    def __init__(self, since_hours: int = 12, fallback_since_hours: int = 24):
        """
        Args:
            since_hours: fetch news from the last N hours
            fallback_since_hours: a longer window used when the last N hours have no news
        """
        self.since_hours = since_hours
        self.fallback_since_hours = fallback_since_hours

    def _dedupe_with_db(self, items: list[NewsItem]) -> list[NewsItem]:
        """Deduplicate with the NewsCache table (works across processes and restarts) so the same item is never sent twice."""
        if not items:
            return []

        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NewsCache

        db = SessionLocal()
        try:
            by_source: dict[str, list[str]] = {}
            for it in items:
                if not it.external_id:
                    continue
                by_source.setdefault(it.source, []).append(it.external_id)

            existing: set[tuple[str, str]] = set()
            for source, ids in by_source.items():
                if not ids:
                    continue
                rows = (
                    db.query(NewsCache.external_id)
                    .filter(NewsCache.source == source, NewsCache.external_id.in_(ids))
                    .all()
                )
                existing.update((source, r[0]) for r in rows)

            new_items: list[NewsItem] = []
            for it in items:
                if it.external_id and (it.source, it.external_id) in existing:
                    continue

                new_items.append(it)
                if it.external_id:
                    # Write to the cache table (content truncated to keep it small)
                    try:
                        db.add(
                            NewsCache(
                                source=it.source,
                                external_id=it.external_id,
                                title=it.title or "",
                                content=(it.content or "")[:2000],
                                publish_time=it.publish_time,
                                symbols=it.symbols or [],
                                importance=it.importance or 0,
                            )
                        )
                    except Exception:
                        # A failed write for one item doesn't affect this run
                        pass

            db.commit()
            return new_items
        except Exception as e:
            logger.warning(f"NewsCache dedupe failed; continuing without it: {e}")
            db.rollback()
            return items
        finally:
            db.close()

    async def collect(self, context: AgentContext) -> dict:
        """Collect news (about the watchlist, plus important market news)."""
        symbols = [stock.symbol for stock in context.watchlist]

        if not symbols:
            logger.warning("Watchlist is empty; skipping news collection")
            return {"news": [], "related_news": [], "watchlist": []}

        collector = NewsCollector.from_database()
        since_hours_used = self.since_hours
        news_list = await collector.fetch_all(
            symbols=symbols,
            since_hours=self.since_hours,
        )
        if (
            not news_list
            and self.fallback_since_hours
            and self.fallback_since_hours > self.since_hours
        ):
            logger.info(
                f"No news in the last {self.since_hours} hours; widening to {self.fallback_since_hours} hours"
            )
            since_hours_used = self.fallback_since_hours
            news_list = await collector.fetch_all(
                symbols=symbols,
                since_hours=self.fallback_since_hours,
            )

        # Dedupe across runs: keep only new items so the agent doesn't repeat itself
        news_list = self._dedupe_with_db(news_list)

        # Split: watchlist news and important market news
        related_news = self._filter_related_news(news_list, symbols)
        important_news = [
            n for n in news_list if n.importance >= 2 and n not in related_news
        ]

        # Structured signals: quote, technicals, flows and positions, for a steadier summary
        packs = {}
        try:
            builder = SignalPackBuilder()
            sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
            packs = await builder.build_for_symbols(
                symbols=sym_list,
                include_news=False,
                news_hours=self.since_hours,
                portfolio=context.portfolio,
                include_technical=True,
                include_capital_flow=True,
                include_events=True,
                events_days=3,
            )
        except Exception as e:
            logger.warning(f"SignalPack failed (news_digest continues): {e}")

        return {
            "news": news_list,  # all news
            "related_news": related_news,  # about the watchlist
            "important_news": important_news,  # important market news
            "watchlist": context.watchlist,
            "signal_packs": packs,
            "timestamp": datetime.now().isoformat(),
            "since_hours_used": since_hours_used,
        }

    def _filter_related_news(
        self, news_list: list[NewsItem], symbols: list[str]
    ) -> list[NewsItem]:
        """Keep news about watchlist stocks."""
        related = []
        for news in news_list:
            # The item is already tagged with symbols
            if news.symbols and any(s in symbols for s in news.symbols):
                related.append(news)
                continue
            # Does the title or content mention a symbol?
            text = news.title + news.content
            if any(s in text for s in symbols):
                related.append(news)

        return related

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the news digest prompt."""
        system_prompt = load_research_prompt("news_digest.txt")

        lines = []
        since_hours_used = data.get("since_hours_used") or self.since_hours
        lines.append(f"## Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## Window: last {since_hours_used} hours\n")

        # Watchlist (holdings marked)
        lines.append("## Watchlist")
        watchlist_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        for stock in context.watchlist:
            pack = packs.get(stock.symbol)
            position = context.portfolio.get_aggregated_position(stock.symbol)

            extra_parts = []
            if pack and pack.quote:
                try:
                    extra_parts.append(
                        f"last {pack.quote.current_price:.2f} ({pack.quote.change_pct:+.2f}%)"
                    )
                except Exception:
                    pass
            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                if tech.get("trend"):
                    extra_parts.append(f"trend {tech.get('trend')}")
                if tech.get("macd_status"):
                    extra_parts.append(f"MACD {tech.get('macd_status')}")
            flow = (pack.capital_flow if pack else None) or {}
            if flow and not flow.get("error") and flow.get("status"):
                extra_parts.append(f"flows {flow.get('status')}")

            extra = (" | " + " ".join(extra_parts)) if extra_parts else ""
            if position:
                lines.append(
                    f"- {stock.name} ({stock.symbol}) [held: {position['total_quantity']} shares]{extra}"
                )
            else:
                lines.append(f"- {stock.name}({stock.symbol}){extra}")

        # Watchlist news
        related_news: list[NewsItem] = data.get("related_news", [])
        lines.append(f"\n## Watchlist news ({len(related_news)})")
        if related_news:
            for news in related_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- No watchlist news")

        # Important market news
        important_news: list[NewsItem] = data.get("important_news", [])
        lines.append(f"\n## Important market news ({len(important_news)})")
        if important_news:
            for news in important_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- No important market news")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _format_news_item(
        self, lines: list[str], news: NewsItem, watchlist_map: dict
    ) -> None:
        """Format one news item."""
        importance_label = ["", "[normal]", "[important]", "[major]"][min(news.importance, 3)]
        time_str = news.publish_time.strftime("%H:%M")
        source_label = news.source

        # Names of the linked stocks
        stock_names = []
        for symbol in news.symbols:
            if symbol in watchlist_map:
                stock_names.append(watchlist_map[symbol].name)
        stock_info = f"[{','.join(stock_names)}] " if stock_names else ""

        link = f" ([source]({news.url}))" if news.url else ""
        lines.append(
            f"- {importance_label} [{source_label} {time_str}] {stock_info}{news.title}{link}"
        )
        if news.content:
            content_brief = news.content[:200] + (
                "..." if len(news.content) > 200 else ""
            )
            lines.append(f"  > {content_brief}")

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
            sym = (getattr(s, "symbol", "") or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym


            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(NEWS_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) A symbol in [...]
            m = re.search(
                r"\[\s*(?P<sym>[A-Za-z0-9&\-]{1,20})\s*\]",
                line,
            )
            sym_raw = m.group("sym") if m else ""

            # 2) A symbol in parentheses, e.g. Infosys (INFY)
            if not sym_raw:
                m = re.search(
                    r"\(\s*(?P<sym>[A-Za-z0-9&\-]{1,20})\s*\)", line
                )
                sym_raw = m.group("sym") if m else ""

            # 3) A symbol at the start of the line
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z0-9&\-]{1,20})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) Fall back to containment
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
                rf"{re.escape(action_text)}\s*[:\\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = NEWS_ACTION_MAP.get(
                action_text, {"action": "watch", "label": "Watch"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:140],
                "should_alert": action_info["action"] in ["alert", "reduce", "sell"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (getattr(s, "symbol", "") or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym

        for it in items:
            if not isinstance(it, dict):
                continue
            sym_raw = (it.get("symbol") or "").strip()
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "watch").strip()
            action_label = (it.get("action_label") or "Watch").strip()
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
                "should_alert": action in ["alert", "reduce", "sell"],
            }
        return suggestions

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Notify when there is watchlist news or important market news."""
        related_news = result.raw_data.get("related_news", [])
        important_news = result.raw_data.get("important_news", [])

        # Watchlist news present
        if related_news:
            return True
        # Important market news present
        if important_news:
            return True
        return False

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """Save the digest to history so it can be viewed in the UI."""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

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

        # Parse per-stock actions and write them to the suggestion pool
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
        stock_map = {s.symbol: s for s in context.watchlist}
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if not stock:
                continue
            save_suggestion(
                stock_symbol=symbol,
                stock_name=stock.name,
                action=sug["action"],
                action_label=sug["action_label"],
                signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                reason=sug.get("reason", ""),
                agent_name=self.name,
                agent_label=self.display_name,
                expires_hours=12,
                prompt_context=user_content,
                ai_response=result.content,
                stock_market=stock.market.value,
                meta={
                    "source": "news_digest",
                    "since_hours_used": data.get("since_hours_used", self.since_hours),
                    "related_count": len(data.get("related_news", []) or []),
                    "important_count": len(data.get("important_news", []) or []),
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

        # Save to history ("*" marks a market-wide item)
        related_news: list[NewsItem] = data.get("related_news", []) or []
        important_news: list[NewsItem] = data.get("important_news", []) or []
        payload_news = []
        for it in (related_news + important_news)[:30]:
            payload_news.append(
                {
                    "source": it.source,
                    "external_id": it.external_id,
                    "title": it.title,
                    "publish_time": it.publish_time.isoformat(),
                    "symbols": it.symbols,
                    "importance": it.importance,
                    "url": it.url,
                }
            )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "since_hours": self.since_hours,
                "since_hours_used": data.get("since_hours_used", self.since_hours),
                "related_count": len(related_news),
                "important_count": len(important_news),
                "news": payload_news,
                "suggestions": suggestions,
                "research": result.raw_data.get("research", []),
                "prompt_context": user_content[:2000],
            },
        )

        return result
