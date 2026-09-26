"""Regression tests for mapping the upstream 5-level rating to Candlewise's 3 actions.

Root-cause bug: the upstream PM uses Buy/Overweight/Hold/Underweight/Sell, but only 3 levels
were recognised; Overweight/Underweight fell back to hold, so the header said "Hold" while the
PM actually said "Underweight".
"""

from __future__ import annotations

from types import SimpleNamespace

from src.modules.automation.tradingagents.decision import (
    RATING_ACTION_MAP,
    RATING_LABEL_MAP,
    _parse_rating_from_text,
    _parse_rating_label,
    map_state_to_result,
)

FULLWIDTH_COLON = chr(0xFF1A)


def _stock():
    return SimpleNamespace(symbol="TATAMOTORS", name="Tata Motors", market=SimpleNamespace(value="IN"))


def _result(decision_raw: str, final_decision_text: str = "") -> dict:
    """Build a ta_result for map_state_to_result."""
    return {
        "decision": decision_raw,
        "final_state": {
            "final_trade_decision": final_decision_text,
            "trader_investment_plan": "Action: Sell\n\nReasoning: high risk",
        },
        "cost_usd": 0.05,
    }


# ============================================================
# 5-level rating -> 3 actions + label
# ============================================================

def test_buy_rating_maps_to_buy():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Buy"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "Buy"


def test_overweight_rating_maps_to_buy_with_own_label():
    """Overweight -> action=buy, but the label says "Overweight" to tell it apart from buy."""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "Overweight"
    assert r.raw_data["suggestion"]["rating_raw"] == "overweight"


def test_hold_rating_maps_to_hold():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold"))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["action_label"] == "Hold"


def test_underweight_rating_maps_to_sell_with_own_label():
    """Key regression: Underweight used to fall back to hold;
    now it is action=sell + label=Underweight."""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Underweight"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "Underweight"
    assert r.raw_data["suggestion"]["rating_raw"] == "underweight"
    # Should alert (unlike hold)
    assert r.raw_data["suggestion"]["should_alert"] is True


def test_sell_rating_maps_to_sell():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Sell"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "Sell"


# ============================================================
# Fallback: propagate() didn't return a 5-level rating, so parse the text
# ============================================================

def test_decision_text_with_rating_label():
    """final_trade_decision with 'Rating: Underweight' -> underweight"""
    text = "After thorough analysis...\n\n**Rating**: Underweight\n\nReason: high leverage"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_final_decision_label():
    """'Final decision: Underweight' -> underweight"""
    text = "All things considered: **Final decision: Underweight**, trim exposure"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_final_transaction_proposal():
    """'FINAL TRANSACTION PROPOSAL: SELL' -> sell"""
    text = "...\n\nFINAL TRANSACTION PROPOSAL: **SELL**"
    assert _parse_rating_from_text(text) == "sell"


def test_decision_text_fallback_to_keyword_scan():
    """A 5-level word is found in the text even without an explicit label."""
    text = "Based on macro headwinds, recommend Overweight position in defensive sectors."
    assert _parse_rating_from_text(text) == "overweight"


def test_decision_empty_falls_back_to_hold():
    """propagate returns nothing and the text has no rating word -> hold by default."""
    r = map_state_to_result(stock=_stock(), ta_result=_result("", final_decision_text=""))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_review_signal_is_preserved_as_manual_review():
    """0.4.0's REVIEW is a non-tradable manual review signal and must not pose as a plain hold."""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="Upstream could not parse a final rating"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"  # keeps the frontend's existing 3-action API
    assert suggestion["action_label"] == "Needs manual review"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["should_alert"] is True
    assert suggestion["upstream_decision"] == "review"


def test_review_signal_overrides_parseable_pm_rating():
    """Upstream REVIEW wins over a rating word in the text and must never become a tradable buy."""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="Rating: Buy"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"
    assert suggestion["action_label"] == "Needs manual review"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["review_required"] is True


def test_decision_unrecognized_then_text_has_underweight():
    """propagate returns an unrecognised 'xxxx' -> underweight parsed from final_decision."""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("xxxx", final_decision_text="...\n**Rating**: Underweight\n..."),
    )
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "Underweight"


# ============================================================
# Text vs upstream decision conflict: the text wins (production regression)
# Upstream propagate re-summarised to "HOLD", but the PM text clearly said buy/sell,
# so the text must win. Models sometimes use a full-width colon; an early regex only
# accepted ":" and fell back to the distorted decision=HOLD. Both colons are covered.
# ============================================================

def test_fullwidth_colon_buy_overrides_hold():
    """decision=HOLD but the text says 'Final trade decision<full-width colon> Buy' -> buy"""
    text = (
        "Having weighed the risk analysts' debate on Tata Motors, "
        "here is my final trade decision:\n\n"
        f"**Final trade decision{FULLWIDTH_COLON} Buy**\n\n**Basis:** 1. Profitability..."
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("HOLD", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "Buy"


def test_fullwidth_colon_sell_overrides_hold():
    """decision=Hold but the text says 'Rating<full-width colon> **Sell**' -> sell"""
    text = f"Risk debate summary...\n\n## Final decision{FULLWIDTH_COLON} **Sell**\n\n### Rating: **Sell**\n\nCore basis..."
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "Sell"
    assert r.raw_data["suggestion"]["rating_raw"] == "sell"


def test_halfwidth_colon_still_works():
    """The ASCII colon keeps working: 'Final trade decision: Buy' -> buy"""
    text = "In short...\n\nFinal trade decision: **Buy**\nRating: Buy"
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"


def test_parse_rating_label_covers_both_colons():
    """_parse_rating_label handles both the full-width and the ASCII colon."""
    assert _parse_rating_label(f"Final trade decision{FULLWIDTH_COLON}Buy") == "buy"
    assert _parse_rating_label("Final trade decision: Buy") == "buy"
    assert _parse_rating_label(f"Rating{FULLWIDTH_COLON}Sell") == "sell"
    assert _parse_rating_label("Rating: Sell") == "sell"
    assert _parse_rating_label("FINAL TRANSACTION PROPOSAL: **BUY**") == "buy"


def test_decision_used_when_text_has_no_label():
    """No explicit rating label in the text -> trust the upstream decision (Hold here)."""
    text = "The market is uncertain; stay on the sidelines and wait for a clearer signal."
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_text_label_not_confused_by_distractor_words():
    """A distractor word (a rejected "buy") but an explicit "sell" label -> the label wins: sell, not buy."""
    text = (
        "I rejected the bull analyst's **buy** idea because the fundamentals are worsening.\n\n"
        "FINAL TRANSACTION PROPOSAL: **SELL**"
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"


# ============================================================
# Markdown rendering: the 5-level label in the markdown header
# ============================================================

def test_markdown_shows_5_tier_rating_in_header():
    """The raw 5-level rating is kept internally, but never shown in research-only text."""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("Underweight", final_decision_text="Rating: Underweight\n\nReason: ..."),
    )
    # The 5-tier mapping is still computed internally (kept for a future reviewed mode)...
    assert r.raw_data["rating"] == "underweight"
    # ...but user-facing text passes the research-only guard, so no rating label survives.
    assert "Underweight" not in r.content


# ============================================================
# raw_data keeps both the 3-level decision and the 5-level rating
# ============================================================

def test_raw_data_has_both_decision_and_rating():
    """Frontend compatibility: the 3-level decision for old code and the 5-level rating for new views."""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["decision"] == "buy"  # 3 levels
    assert r.raw_data["rating"] == "overweight"  # 5 levels


# ============================================================
# Static mapping completeness
# ============================================================

def test_all_5_ratings_have_label():
    for r in ("buy", "overweight", "hold", "underweight", "sell"):
        assert r in RATING_LABEL_MAP
        assert r in RATING_ACTION_MAP


def test_action_map_only_uses_3_actions():
    """The 3 actions can only be buy/hold/sell (the frontend type)."""
    assert set(RATING_ACTION_MAP.values()) == {"buy", "hold", "sell"}


# ============================================================
# Markdown completeness: the output of all 9 agents is represented
# ============================================================

def _full_state():
    return {
        "final_trade_decision": "**Rating: Underweight** detailed decision...",
        "trader_investment_plan": "Action: Sell\nCut the position by 70%",
        "risk_judge_decision": "Risk debate: after the aggressive/conservative/neutral discussion, stay cautious",
        "investment_debate_state": {
            "history": "Bull: ...\nBear: ...\nBull: ...\nBear: ...",
            "judge_decision": "Research manager: weighing bull and bear, leaning to a cautious hold",
        },
        "market_report": "Technicals: MACD death cross, bearish alignment, weak trend..." * 30,
        "social_report": "Sentiment: less discussion, more bearish voices..." * 20,
        "news_report": "News: announcement: Q1 net profit down..." * 20,
        "fundamentals_report": "Fundamentals: revenue up but margins down, ROE negative..." * 20,
    }


def test_markdown_contains_decision_chain():
    """The decision chain (PM/trader/research manager/risk) stays out of content; full analyst reports are in raw_data for the frontend tabs."""
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Underweight", "final_state": _full_state(), "cost_usd": 0.05},
    )
    content = r.content
    # Research-only: the decision chain (PM/trader/research manager/risk verdict) never
    # reaches user-facing content; the guard withholds it.
    assert "PM decision" not in content
    assert "Trader's plan" not in content
    assert "leaning to a cautious hold" not in content
    # Full analyst reports are in raw_data, rendered by frontend tabs
    reports = r.raw_data["analyst_reports"]
    assert reports["market"] and reports["social"] and reports["news"] and reports["fundamentals"]


def test_analyst_reports_full_not_truncated():
    """Analyst reports are kept in full in raw_data, not truncated (fixes financial tables cut at the header)."""
    state = _full_state()
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    # Identical to the original reports, no truncation at all
    assert reports["market"] == state["market_report"]
    assert reports["fundamentals"] == state["fundamentals_report"]
    assert len(reports["market"]) > 300  # well past the old 300-character overview limit


def test_empty_analyst_kept_empty_in_raw_data():
    """An analyst with no output -> an empty string in raw_data (the frontend skips that tab)."""
    state = _full_state()
    state["social_report"] = ""  # the sentiment analyst didn't run
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    assert reports["social"] == ""
    assert reports["market"] and reports["news"] and reports["fundamentals"]


def test_markdown_skips_judge_when_no_debate():
    """Without a debate history, no "Research manager's ruling" section is rendered."""
    state = _full_state()
    state["investment_debate_state"] = {"history": "", "judge_decision": ""}
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    assert "Research manager's ruling" not in r.content


# ============================================================
# Sentiment analyst field (upstream sentiment_report) + full notification content
# ============================================================

def test_sentiment_report_maps_to_social():
    """The upstream sentiment field is sentiment_report and must map to analyst_reports.social."""
    state = {"final_trade_decision": "Rating: Buy", "sentiment_report": "Sentiment: more discussion, more bulls"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "Sentiment: more discussion, more bulls"


def test_social_report_fallback_when_no_sentiment():
    """The old social_report field still works (fallback without sentiment_report)."""
    state = {"final_trade_decision": "Rating: Hold", "social_report": "old sentiment field content"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "old sentiment field content"


def test_notify_content_only_final_decision():
    """The notification body (notify_content) holds only the final decision part.
    Trader plan / research manager ruling / risk debate / the four analysts stay out of notifications
    (so they aren't cut for length) and appear only on the detail page."""
    state = {
        "final_trade_decision": "Final trade decision: Buy\n\nCore logic: fundamentals turning, valuation re-rating ahead",
        "trader_investment_plan": "Trader plan: build in three tranches, first tranche 30%",
        "market_report": "technical analysis details" * 100,
        "sentiment_report": "sentiment details" * 100,
        "investment_debate_state": {"history": "bull/bear debate history", "judge_decision": "Research manager ruling: leaning bullish"},
        "risk_debate_state": {"history": "three-way risk debate", "judge_decision": "Risk team conclusion: position manageable"},
    }
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    # The notification body is set on its own (no longer falls back to content)
    assert r.notify_content is not None
    nc = r.notify_content
    # Research-only: the PM decision never reaches a notification.
    assert "Buy" not in nc
    assert "fundamentals turning" not in nc
    # No trader plan / ruling / risk / analyst details
    assert "three tranches" not in nc
    assert "leaning bullish" not in nc
    assert "position manageable" not in nc
    assert state["market_report"] not in nc
    assert "Final trade decision: Buy" not in r.content


# ============================================================
# Confidence A+B: an explicit PM number first (either colon), otherwise derived from the rating
# ============================================================

def test_confidence_extracted_fullwidth_colon():
    """'Confidence<full-width colon>8.5/10' yields the real value (early code only accepted ':')."""
    state = {"final_trade_decision": f"Rating: Buy\nConfidence{FULLWIDTH_COLON}8.5/10"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 8.5


def test_confidence_extracted_halfwidth():
    """'confidence: 7' is still found."""
    state = {"final_trade_decision": "Rating: Buy\nconfidence: 7"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 7.0


def test_confidence_derived_from_rating_when_absent():
    """No explicit confidence -> derived from the rating (strong direction > neutral), not a flat 5.0."""
    buy = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": {"final_trade_decision": "Final trade decision: Buy"}, "cost_usd": 0.01})
    hold = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": {"final_trade_decision": "Final trade decision: Hold"}, "cost_usd": 0.01})
    sell = map_state_to_result(stock=_stock(), ta_result={"decision": "Sell", "final_state": {"final_trade_decision": "Rating: Sell"}, "cost_usd": 0.01})
    assert buy.raw_data["suggestion"]["confidence"] == 7.0
    assert hold.raw_data["suggestion"]["confidence"] == 5.0
    assert sell.raw_data["suggestion"]["confidence"] == 7.0
    assert buy.raw_data["suggestion"]["confidence"] > hold.raw_data["suggestion"]["confidence"]
