"""Map TradingAgents output to a PanWatch AnalysisResult.

TradingAgents' ``final_state`` is the dict LangGraph accumulates. Key fields (from upstream):
- market_report / social_report / news_report / fundamentals_report: the four analyst reports
- investment_debate_state: bull/bear debate {history, current_response, judge_decision}
- trader_investment_plan: the trader's view
- risk_judge_decision: the risk verdict
- final_trade_decision: the portfolio manager's final write-up
- (processed_signal): "BUY" / "HOLD" / "SELL"

After the result is saved, the second half of this file bridges optional simulation
signals; both share the same REVIEW safety mapping. Used only in recommendation mode;
research_only runs use research_graph.py.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from src.modules.automation.base import AnalysisResult
from src.platform.compliance import ensure_guarded

__all__ = [
    "DECISION_LABEL_MAP",
    "map_state_to_research_result",
    "map_state_to_result",
    "maybe_emit_paper_trading_signal",
]


# Upstream five-level rating -> display label
RATING_LABEL_MAP = {
    "buy": "Buy",
    "overweight": "Overweight",
    "hold": "Hold",
    "underweight": "Underweight",
    "sell": "Sell",
}

# Five levels -> three (the action field; the UI knows 'buy' | 'hold' | 'sell')
RATING_ACTION_MAP = {
    "buy": "buy",
    "overweight": "buy",
    "hold": "hold",
    "underweight": "sell",
    "sell": "sell",
}

# Since 0.4.0 upstream returns REVIEW when the PM rating can't be parsed. It is not a
# tradable Hold: it maps to hold (blocking automation) and keeps the raw state for display.
REVIEW_RATING = "review"
REVIEW_LABEL = "Needs manual review"

# Legacy name: some callers import DECISION_LABEL_MAP
DECISION_LABEL_MAP = RATING_LABEL_MAP


_REPORT_FIELDS = (
    ("market", "market_report"),
    ("social", "sentiment_report"),
    ("news", "news_report"),
    ("fundamentals", "fundamentals_report"),
)


def map_state_to_research_result(
    *,
    stock: Any,
    ta_result: dict[str, Any],
    model_label: str = "",
) -> AnalysisResult:
    """Research-only mapping (ADR-005): a neutral summary, analyst reports and the
    bull/bear debate. No rating, action, trader plan, risk verdict or PM decision.
    """
    state = ta_result.get("final_state") or {}
    cost_usd = float(ta_result.get("cost_usd", 0.0) or 0.0)
    summary = ensure_guarded(
        (state.get("final_trade_decision") or "").strip()
        or "No research summary was produced for this run.",
        surface="ta_summary",
    )
    reports = {
        key: ensure_guarded(
            state.get(field) or (state.get("social_report") if key == "social" else "") or "",
            surface="ta_report",
        )
        for key, field in _REPORT_FIELDS
    }
    debate_state = state.get("investment_debate_state") or {}
    if not isinstance(debate_state, dict):
        debate_state = {}
    debate = {
        "history": ensure_guarded(debate_state.get("history") or "", surface="ta_debate"),
        "bull_history": ensure_guarded(debate_state.get("bull_history") or "", surface="ta_debate"),
        "bear_history": ensure_guarded(debate_state.get("bear_history") or "", surface="ta_debate"),
    }

    from datetime import date as _date
    from src.modules.research.analysis_link import analysis_detail_markdown

    link = analysis_detail_markdown(stock.symbol, _date.today().isoformat())
    parts = [f"## Deep research: {stock.name} ({stock.symbol})", "", summary]
    footer = "_Multi-agent research (analysts and bull/bear debate)"
    if model_label:
        footer += f" · AI: {model_label}"
    parts += ["", "---", footer + f" · cost ${cost_usd:.4f}_"]
    if link:
        parts += ["", link]
    content = "\n".join(parts)
    notify_parts = [summary]
    if link:
        notify_parts += ["", link]

    return AnalysisResult(
        agent_name="tradingagents",
        title=f"Deep research: {stock.name} ({stock.symbol})",
        content=content,
        notify_content="\n".join(notify_parts),
        raw_data={
            "mode": "research_only",
            "research_summary": summary,
            "cost_usd": cost_usd,
            "analyst_reports": reports,
            "debate_history": debate,
        },
    )


def map_state_to_result(
    *,
    stock: Any,
    ta_result: dict[str, Any],
    model_label: str = "",
) -> AnalysisResult:
    """Entry point: map TradingAgents' final_state to an AnalysisResult.

    Args:
        stock: PanWatch StockConfig(symbol/name/market)
        ta_result: {"decision": str, "final_state": dict, "cost_usd": float}
        model_label: e.g. "deepseek/deepseek-chat", appended to the markdown
    """
    state = ta_result.get("final_state") or {}
    cost_usd = float(ta_result.get("cost_usd", 0.0) or 0.0)

    # The PM text (final_trade_decision, what the user actually reads) is authoritative:
    # upstream propagate()'s second return value re-summarises it and can drift (text says
    # "Sell" but returns "HOLD"), so an explicit rating label in the text wins.
    # Priority: explicit label in the text > upstream decision > loose scan of the text.
    final_text = state.get("final_trade_decision") or ""
    upstream_rating = (ta_result.get("decision") or "").strip().lower()
    if upstream_rating == REVIEW_RATING:
        # REVIEW means upstream could not parse the PM output; rating words in the text must not override it.
        rating_raw = REVIEW_RATING
    else:
        rating_raw = _parse_rating_label(final_text)
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = upstream_rating
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = _parse_rating_from_text(final_text)

    review_required = rating_raw == REVIEW_RATING
    action = RATING_ACTION_MAP.get(rating_raw, "hold")
    action_label = REVIEW_LABEL if review_required else RATING_LABEL_MAP.get(rating_raw, "Hold")

    confidence = _extract_confidence(state, rating_raw)
    short_reason = _short_reason(state)

    suggestion = {
        "action": action,
        "action_label": action_label,
        "rating_raw": rating_raw or "hold",  # keep the raw five-level rating for the UI and history
        "review_required": review_required,
        "upstream_decision": upstream_rating,
        "signal": _truncate(state.get("trader_investment_plan", ""), 200),
        "reason": state.get("final_trade_decision") or short_reason,
        "should_alert": review_required or rating_raw in ("buy", "overweight", "underweight", "sell"),
        "agent_name": "tradingagents",
        "agent_label": "TradingAgents deep research",
        "confidence": confidence,
    }

    content = _render_markdown(state, suggestion, model_label, cost_usd)
    # Link to the detail page (only with panwatch_base_url set)
    from datetime import date as _date
    from src.modules.research.analysis_link import analysis_detail_markdown
    _link = analysis_detail_markdown(stock.symbol, _date.today().isoformat())
    if _link:
        content = content.rstrip() + f"\n\n---\n{_link}"
    # Notifications carry only the final decision (summary + PM write-up) and the link;
    # the trader, research manager, risk debate and analyst reports stay on the detail page
    # so channels don't truncate the message.
    notify_content = _render_notify(state, suggestion, cost_usd, _link)

    return AnalysisResult(
        agent_name="tradingagents",
        title=f"[Deep research] {stock.name} ({stock.symbol}): {suggestion['action_label']}",
        content=content,
        notify_content=notify_content,
        raw_data={
            "suggestion": suggestion,
            "cost_usd": cost_usd,
            "should_alert": suggestion["should_alert"],
            "decision": action,           # legacy three-level field
            "rating": rating_raw or "hold",  # raw five-level field
            "upstream_decision": upstream_rating,
            "confidence": confidence,
            "debate_history": _extract_debate(state),
            "risk_judgment": _risk_judgment(state),
            "risk_debate": _extract_risk_debate(state),
            "analyst_reports": {
                "market": state.get("market_report") or "",
                # Upstream's sentiment analyst writes sentiment_report; social_report is the legacy name
                "social": state.get("sentiment_report") or state.get("social_report") or "",
                "news": state.get("news_report") or "",
                "fundamentals": state.get("fundamentals_report") or "",
            },
            "final_decision": state.get("final_trade_decision") or "",
            "trader_plan": state.get("trader_investment_plan") or "",
        },
    )


# ---- helpers ----


# Explicit rating label such as "Rating: Buy" or "FINAL TRANSACTION PROPOSAL: **Hold**"
# (a full-width colon, U+FF1A, is accepted too).
_RATING_TEXT_RE = re.compile(
    r"(?:Rating|Final\s+(?:Trade\s+)?Decision|FINAL\s+TRANSACTION\s+PROPOSAL)"
    r"[\s\*:\uff1a\-—]+(\*\*)?\s*(Buy|Overweight|Hold|Underweight|Sell)",
    re.I,
)


def _parse_rating_label(text: str) -> str:
    """Parse only an **explicit rating label** in the PM text (Rating / Final Decision /
    FINAL TRANSACTION PROPOSAL: X).

    No loose keyword scan, so text such as "rejected the earlier buy idea" can't be misread.
    Tried first, so the displayed rating matches the write-up the user reads.
    """
    if not text:
        return ""
    m = _RATING_TEXT_RE.search(text)
    if m:
        word = m.group(2).lower()
        if word in RATING_LABEL_MAP:
            return word
    return ""


def _parse_rating_from_text(text: str) -> str:
    """Extract a five-level rating: a 'Rating: X' label first, then the first rating word."""
    if not text:
        return ""
    label = _parse_rating_label(text)
    if label:
        return label
    # Fallback: the first rating word anywhere in the text
    text_low = text.lower()
    for word in ("overweight", "underweight", "buy", "sell", "hold"):
        if word in text_low:
            return word
    return ""


# Confidence such as "Confidence: 8" or "confidence 7/10".
_CONFIDENCE_PATTERNS = [
    re.compile(r"confidence[:\uff1a\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
]

# Without an explicit number, derive a base confidence from the rating instead of a flat
# 5.0: strong directions (buy/sell) higher, neutral (hold) in the middle.
_RATING_CONFIDENCE_FALLBACK = {
    "buy": 7.0,
    "sell": 7.0,
    "overweight": 6.0,
    "underweight": 6.0,
    "hold": 5.0,
}


def _extract_confidence(state: dict, rating_raw: str = "") -> float:
    """Confidence (0-10): an explicit number in the PM, risk or trader text first;
    otherwise derived from the rating instead of a flat 5.0."""
    candidates = [
        state.get("final_trade_decision", ""),
        _risk_judgment(state),
        state.get("trader_investment_plan", ""),
    ]
    for text in candidates:
        if not text:
            continue
        for pat in _CONFIDENCE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 10:  # a percentage: convert to 0-10
                        v = v / 10
                    return max(0.0, min(10.0, v))
                except (ValueError, IndexError):
                    continue
    # Fallback: derive from the rating (5.0 only without a recognisable rating)
    return _RATING_CONFIDENCE_FALLBACK.get(rating_raw, 5.0)


def _short_reason(state: dict, limit: int = 120) -> str:
    """A short reason: the first 120 characters of final_trade_decision first."""
    candidates = [
        state.get("final_trade_decision") or "",
        state.get("trader_investment_plan") or "",
        _risk_judgment(state),
    ]
    for text in candidates:
        text = text.strip()
        if text:
            return _truncate(text, limit)
    return ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_debate(state: dict) -> dict:
    """Extract the debate. Upstream investment_debate_state is roughly:
    {
        "history": "...",      # the full debate text
        "current_response": ...,
        "judge_decision": ...,
    }
    """
    debate = state.get("investment_debate_state") or {}
    if not isinstance(debate, dict):
        return {}
    return {
        "history": debate.get("history", ""),
        "current_response": debate.get("current_response", ""),
        "judge_decision": debate.get("judge_decision", ""),
    }


def _risk_judgment(state: dict) -> str:
    """The risk team's verdict: upstream stores it in risk_debate_state.judge_decision (after the
    aggressive/neutral/conservative debate). There is no top-level risk_judge_decision field."""
    rds = state.get("risk_debate_state")
    if isinstance(rds, dict):
        jd = (rds.get("judge_decision") or "").strip()
        if jd:
            return jd
    return (state.get("risk_judge_decision") or "").strip()  # legacy fallback


def _extract_risk_debate(state: dict) -> dict:
    """The risk team's debate (aggressive/neutral/conservative plus verdict), like _extract_debate.
    Upstream risk_debate_state.history holds the full three-way debate text."""
    rds = state.get("risk_debate_state")
    if not isinstance(rds, dict):
        return {}
    return {
        "history": rds.get("history", ""),
        "judge_decision": rds.get("judge_decision", ""),
    }


def _render_notify(
    state: dict, suggestion: dict, cost_usd: float, link_md: str = ""
) -> str:
    """Notification body: only the final decision (summary + PM write-up) and the detail link.

    The trader plan, research manager verdict, risk debate and analyst reports stay on the
    detail page, keeping notifications short enough that channels don't truncate them.
    """
    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(rating: {RATING_LABEL_MAP.get(rating_raw, 'Hold')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts = [
        f"## Final decision\n\n"
        f"**{suggestion['action_label']}** {rating_note} · confidence {suggestion['confidence']:.1f}/10\n"
    ]
    final_text = (state.get("final_trade_decision") or "").strip()
    if final_text:
        parts.append(final_text + "\n")
    parts.append(
        f"\n_Cost ${cost_usd:.4f} · the trader, research manager, risk debate and analyst reports are on the detail page_"
    )
    if link_md:
        parts.append(f"\n\n{link_md}")
    return "\n".join(parts)


def _render_markdown(
    state: dict, suggestion: dict, model_label: str, cost_usd: float
) -> str:
    parts = []

    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(rating: {RATING_LABEL_MAP.get(rating_raw, 'Hold')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts.append(
        f"## Final decision\n\n"
        f"**{suggestion['action_label']}** {rating_note} · confidence {suggestion['confidence']:.1f}/10\n"
    )

    # The nine-agent chain: PM (write-up) -> trader -> research manager -> risk -> four analysts
    if state.get("final_trade_decision"):
        parts.append(f"### 🎯 Portfolio manager's decision\n\n{state['final_trade_decision']}\n")

    if state.get("trader_investment_plan"):
        parts.append(f"### 💼 Trader's plan\n\n{state['trader_investment_plan']}\n")

    # Research manager's verdict after the bull/bear debate
    debate = state.get("investment_debate_state") or {}
    judge_decision = ""
    if isinstance(debate, dict):
        judge_decision = (debate.get("judge_decision") or "").strip()
    if judge_decision:
        parts.append(f"### ⚖️ Research manager's verdict (bull vs bear)\n\n{judge_decision}\n")

    risk_jd = _risk_judgment(state)
    if risk_jd:
        parts.append(f"### 🛡️ Risk debate verdict\n\n{risk_jd}\n")

    # The four analyst reports are not in this markdown (truncating cut tables mid-way);
    # they live in raw_data.analyst_reports and the UI renders them in full (GFM tables).

    parts.append(
        "\n---\n"
        f"_Generated by the TradingAgents nine-agent framework (technical / sentiment / news / "
        f"fundamentals -> bull/bear debate -> research manager -> trader -> risk debate -> PM). "
        f"For research and education only; not investment advice._\n"
        f"\nCost: ${cost_usd:.4f}"
    )
    if model_label:
        parts.append(f" · AI:{model_label}")

    return "\n".join(parts)


# ============================================================================
# Paper trading bridge
# ============================================================================

logger = logging.getLogger(__name__)


def maybe_emit_paper_trading_signal(
    *,
    stock_symbol: str,
    stock_market: str,
    stock_name: str,
    decision: str,
    confidence: float,
    signal_text: str,
    reason: str,
    current_price: float | None,
    enabled: bool,
) -> bool:
    """Write a TradingAgents decision to StrategySignalRun. Returns whether it wrote one.

    - Only with enabled=True and decision in (buy, add) (SELL never opens a position)
    - entry_low/high: the current price +/- 2%
    - stop_loss: entry -5%; target_price: +10% (coarse)
    - Deduped per stock and day: strategy_code + source_candidate_id are unique
    Disabled in research_only (ADR-004).
    """
    if not enabled:
        return False
    action = (decision or "").lower()
    if action not in ("buy", "add"):
        return False
    if not current_price or current_price <= 0:
        logger.warning(
            f"[TA paper] {stock_symbol} has no current price; not writing a signal"
        )
        return False

    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import StrategySignalRun

    snapshot_date = date.today().isoformat()
    entry_low = round(current_price * 0.98, 2)
    entry_high = round(current_price * 1.02, 2)
    stop_loss = round(current_price * 0.95, 3)
    target_price = round(current_price * 1.10, 3)

    db = SessionLocal()
    try:
        # Repeat triggers for a stock on the same day upsert (source_candidate_id 0 is the TA sentinel)
        source_id = 0
        existing = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date == snapshot_date,
                StrategySignalRun.stock_symbol == stock_symbol,
                StrategySignalRun.stock_market == stock_market,
                StrategySignalRun.strategy_code == "tradingagents",
                StrategySignalRun.source_candidate_id == source_id,
            )
            .first()
        )
        if existing:
            existing.action = action
            existing.action_label = DECISION_LABEL_MAP.get(action, "Buy")
            existing.signal = signal_text[:500]
            existing.reason = reason[:1000]
            existing.confidence = confidence
            existing.entry_low = entry_low
            existing.entry_high = entry_high
            existing.stop_loss = stop_loss
            existing.target_price = target_price
            existing.status = "active"
        else:
            row = StrategySignalRun(
                snapshot_date=snapshot_date,
                stock_symbol=stock_symbol,
                stock_market=stock_market,
                stock_name=stock_name or stock_symbol,
                strategy_code="tradingagents",
                strategy_name="TradingAgents deep research",
                strategy_version="v1",
                risk_level="medium",
                source_pool="watchlist",
                score=float(confidence or 5.0),
                rank_score=float(confidence or 5.0) * 10,  # a mid-range score
                confidence=float(confidence or 5.0) / 10,
                status="active",
                action=action,
                action_label=DECISION_LABEL_MAP.get(action, "Buy"),
                signal=signal_text[:500],
                reason=reason[:1000],
                evidence=[],
                holding_days=10,  # TradingAgents' horizon is medium term
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_price=target_price,
                invalidation="price below the stop level / fundamentals deteriorate",
                plan_quality=70,
                source_agent="tradingagents",
                source_candidate_id=source_id,
            )
            db.add(row)
        db.commit()
        logger.info(
            f"[TA paper] Signal written: {stock_symbol} {action} "
            f"entry=[{entry_low}, {entry_high}] stop={stop_loss} target={target_price}"
        )
        return True
    except Exception as e:
        logger.warning(f"[TA paper] Writing StrategySignalRun failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()
