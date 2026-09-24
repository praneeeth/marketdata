"""Adversarial corpus for the research-only output guard (PLAN.md Phase 1g).

FORBIDDEN samples must never reach a user unchanged: each is redacted or blocked, and
the guarded text contains no detectable advice. ALLOWED samples are factual research
text that must pass unchanged (false-positive budget: at least 98%).
"""

from __future__ import annotations

import pytest

from src.platform.compliance import guard_text
from src.platform.compliance.detector import detect

FORBIDDEN: tuple[str, ...] = (
    # Directives
    "You should buy Reliance Industries at current levels.",
    "I recommend accumulating HDFC Bank on dips.",
    "We advise investors to exit Paytm immediately.",
    "Investors should consider reducing exposure to IT stocks.",
    "Traders can short Bank Nifty below 51,000.",
    "It's advisable to buy on dips.",
    "Consider adding more Titan on declines.",
    "Book profits in Adani Ports.",
    "Hold for the long term.",
    "Continue to hold the stock.",
    "Reduce exposure to metals.",
    "Avoid this stock for now.",
    "Sell Infosys.",
    "Buy TCS now.",
    "Accumulate Larsen on every dip.",
    "Stay invested in quality large caps.",
    "You may want to switch from ICICI Bank to Axis Bank.",
    "Existing holders can average down near 900.",
    "This stock should be bought before results.",
    "Increase your allocation to pharma.",
    # Ratings
    "Strong buy on Tata Motors.",
    "Rating: Overweight.",
    "Maintain Buy rating on ITC.",
    "Brokerage X upgraded the stock to Buy from Hold.",
    "Outperform rating reiterated.",
    "Our recommendation is to accumulate.",
    "MACD gave a buy signal on the daily chart.",
    # Price targets and forecasts
    "Buy TCS with a target of ₹4,200 and a stop loss at ₹3,650.",
    "Target price: 1,850.",
    "Our price target for Infosys is Rs 2,100.",
    "TP 2,380 in three months.",
    "Nifty could rally to 26,500 by expiry.",
    "We expect the stock to reach 3,000.",
    "Fair value of ₹1,950 implies 18% upside.",
    "The stock has 25% upside from here.",
    "Upside potential of 30% over 12 months.",
    # Stop-losses
    "Keep a strict stop-loss of 1,480.",
    "SL 2140, TGT 2380.",
    "Trailing stop at 5% below the recent high.",
    "Exit if it closes below 1,400.",
    "Place stops at 1,210.",
    # Entry levels
    "Entry around ₹1,520 looks attractive.",
    "The buying zone is between 1,200 and 1,250.",
    "Accumulate between 850 and 870.",
    "Initiate a long position above 1,300.",
    "Take a fresh position once it breaks out.",
    # Position sizes
    "Allocate 10% of your portfolio to this stock.",
    "Buy 50 shares of SBI.",
    "Go long on Nifty with 2 lots of futures.",
    "Limit exposure to 5% per stock.",
    "Position sizing: keep it small.",
    # Timing and promises
    "This is a great time to buy Asian Paints.",
    "Now is the right time to invest in PSU banks.",
    "A great buying opportunity is here.",
    "Don't miss this chance before it's too late.",
    "This multibagger can double your money.",
    "Guaranteed returns of 20% in 3 months.",
    "A sure-shot winner for Diwali.",
    "Stocks to buy this week: Reliance, Infosys.",
    "Top picks for the festive season.",
    # Obfuscation
    "B.U.Y Reliance now.",
    "Ｂｕｙ Ｒｅｌｉａｎｃｅ ｎｏｗ.",
    "Bu​y Infosys at 1500.",
    "**Buy** HDFC Bank.",
    "Tаrget price ₹2,000.",
    "stop_loss: 1200",
    "b u y   w i p r o",
    "Buy-the-dip in HUL.",
    "> You should sell ICICI Bank.",
    "- Buy Maruti on declines",
    # Hinglish
    "Reliance abhi khareed lo, target 3000.",
    "Isko bech do jaldi.",
    "Profit book karo aur nikal jao.",
    # Chinese (transitional, until Phase 4)
    "建议买入贵州茅台，目标价1800元。",
    "可逢低加仓。",
    "设置止损位 1650。",
    "维持增持评级。",
    "最终决策：持有。",
)

ALLOWED: tuple[str, ...] = (
    "Reliance closed at ₹2,950.40, up 1.2%, on volumes 30% above its 20-day average.",
    "The stock is trading above its 50-day and 200-day moving averages.",
    "Recent support is near ₹2,880 and resistance near ₹3,020 (descriptive levels).",
    "RSI stands at 64, below the 70 level often described as overbought.",
    "The 52-week high is ₹3,217 and the 52-week low is ₹2,220.",
    "FIIs sold ₹2,300 crore and DIIs bought ₹1,900 crore on Monday.",
    "The board approved a buyback at ₹1,500 per share.",
    "A sell-off in IT stocks dragged the Nifty IT index down 2.1%.",
    "Exit polls point to a close contest in the state election.",
    "Short covering was seen in bank stocks during the last hour.",
    "Promoters hold 50.3% of the shares; pledged shares fell to 2%.",
    "HDFC Bank is 30% of your portfolio and banks are 45% overall.",
    "The company targets revenue of ₹10,000 crore by FY27.",
    "Management reiterated its margin target of 18% for FY26.",
    "Quarterly results are due on 24 October; the record date for the dividend is 30 October.",
    "Nifty 50 fell 0.8% while India VIX rose 6%.",
    "The order book rose 14% year on year to ₹45,000 crore.",
    "The stock hit its upper circuit of 5% in the opening minutes.",
    "Brent crude rose 1.4% overnight and USD/INR was steady at 83.9.",
    "RBI kept the repo rate unchanged at 6.5%.",
    "The bull case rests on 20% revenue growth; the bear case cites rising debt.",
    "Key risk: a slowdown in US technology spending could weigh on orders.",
    "The stock fell 12% over the past month after a weak earnings update.",
    "Open interest in Nifty futures rose 8% as open positions increased.",
    "The IPO was subscribed 45 times; the listing date is 3 October.",
    "Mutual funds cut exposure to IT stocks in August, according to AMFI data.",
    "The company plans to invest ₹5,000 crore in new capacity.",
    "Profit booking was seen in auto stocks.",
    "SEBI issued a circular on algorithmic trading on 12 September.",
    "The holding company discount narrowed this quarter.",
    "The stock added 3% on Tuesday after the order win.",
    "Entry of new players has increased competition in the segment.",
    "Buying interest emerged in PSU banks after the announcement.",
    "Selling pressure intensified in metal stocks.",
    "The MACD line crossed above its signal line on the daily chart.",
    "Volumes were 1.8 times the 20-day average.",
    "The stock trades at 24 times trailing earnings versus a five-year median of 28.",
    "Net profit rose 18% to ₹1,240 crore; revenue grew 9%.",
    "Delivery volumes were 42% of traded quantity.",
    "The derivatives expiry is on Thursday.",
    "Analysts expect margins to improve as input costs ease.",
    "The index has fallen for five straight sessions.",
    "GIFT Nifty was trading 40 points lower at 7:45 am IST.",
    "Promoter holding increased by 1.2 percentage points in the June quarter.",
    "The company will report results after market hours on Friday.",
    "Source: NSE corporate announcement, 23 September 2026, 18:42 IST.",
    "Bharti Airtel's average revenue per user rose to ₹211.",
    "The stock has underperformed the Nifty by 7% this year.",
    "Crude oil prices remain a key risk for paint makers.",
    "Liquidity is thin, with an average daily turnover of ₹3 crore.",
)


@pytest.mark.parametrize("text", FORBIDDEN)
def test_forbidden_samples_never_pass(text: str) -> None:
    result = guard_text(text, surface="adversarial")
    assert result.status != "passed", text
    assert result.findings, text
    assert detect(result.text) == [], result.text


def test_forbidden_samples_embedded_in_research_text_are_caught() -> None:
    padding = "\n".join(ALLOWED[:12])
    for text in FORBIDDEN:
        result = guard_text(f"{padding}\n{text}\n{padding}", surface="adversarial")
        assert result.status in {"redacted", "blocked"}, text
        assert text not in result.text
        assert detect(result.text) == []


@pytest.mark.parametrize("text", ALLOWED)
def test_allowed_samples_pass_unchanged(text: str) -> None:
    result = guard_text(text, surface="allowed")
    assert result.status == "passed", (text, result.rule_ids)
    assert result.text == text


def test_false_positive_budget() -> None:
    passed = sum(guard_text(t, surface="allowed").status == "passed" for t in ALLOWED)
    assert passed / len(ALLOWED) >= 0.98


def test_research_document_survives_with_single_redaction() -> None:
    document = "\n".join([*ALLOWED[:10], "You should buy the stock now."])
    result = guard_text(document, surface="document")
    assert result.status == "redacted"
    assert "You should buy" not in result.text
    for line in ALLOWED[:10]:
        assert line in result.text
