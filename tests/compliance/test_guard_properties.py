"""Property-based tests for the output guard (PLAN.md Phase 1g).

Generators compose advice sentences from directives, ratings, targets, stop-losses,
entry levels and position sizes, with tickers, price formats and obfuscations. The
properties hold for every generated input:

* guarded output never contains detectable advice;
* the guard is idempotent and never raises, whatever the input;
* blocked output never contains the original text;
* descriptive research sentences survive unchanged.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.platform.compliance import BLOCKED_MESSAGE, guard_text
from src.platform.compliance.detector import detect

TICKERS = ["Reliance", "Infosys", "HDFC Bank", "TCS", "ITC", "Nifty", "Bank Nifty", "SBI"]
PRICES = ["1,250", "₹1,250", "Rs 1250", "Rs. 1,250.50", "INR 1250", "1250/-", "2,130.5"]

ADVICE_TEMPLATES = [
    "You should buy {t} now.",
    "You should sell {t} above {p}.",
    "We recommend accumulating {t}.",
    "Investors should exit {t}.",
    "Buy {t} with a target of {p}.",
    "Sell {t} with a stop loss at {p}.",
    "Target price for {t}: {p}.",
    "Maintain a buy rating on {t}.",
    "Strong buy: {t}.",
    "Keep a strict stop-loss of {p} on {t}.",
    "Entry around {p} for {t} looks attractive.",
    "Accumulate {t} between {p} and {p}.",
    "Allocate 10% of your portfolio to {t}.",
    "Buy 100 shares of {t}.",
    "This is a great time to buy {t}.",
    "{t} is a multibagger that can double your money.",
    "{t} could rally to {p}.",
    "We expect {t} to reach {p}.",
    "Book profits in {t}.",
    "Traders can short {t} below {p}.",
]

FACT_TEMPLATES = [
    "{t} closed at {p}, up 1.4% on the day.",
    "{t} is trading above its 200-day moving average.",
    "Recent support for {t} is near {p} and resistance is higher.",
    "{t} reported an 11% rise in quarterly revenue.",
    "The 52-week high for {t} is {p}.",
    "Volumes in {t} were twice the 20-day average.",
    "{t} fell 3% after its results announcement.",
]


def _obfuscate(text: str, style: int) -> str:
    if style == 0:
        return text
    if style == 1:
        return text.upper()
    if style == 2:
        return "**" + text + "**"
    if style == 3:
        return text.replace("u", "u​").replace("e", "e‌")
    if style == 4:
        full_width = {c: chr(ord(c) + 0xFEE0) for c in "abcdefghijklmnopqrstuvwxyz"}
        return "".join(full_width.get(c, c) for c in text)
    if style == 5:
        return "- " + text
    return "> " + text


advice = st.builds(
    lambda template, ticker, price, style: _obfuscate(template.format(t=ticker, p=price), style),
    st.sampled_from(ADVICE_TEMPLATES),
    st.sampled_from(TICKERS),
    st.sampled_from(PRICES),
    st.integers(min_value=0, max_value=6),
)
facts = st.builds(
    lambda template, ticker, price: template.format(t=ticker, p=price),
    st.sampled_from(FACT_TEMPLATES),
    st.sampled_from(TICKERS),
    st.sampled_from(PRICES),
)

SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SETTINGS
@given(advice)
def test_advice_never_survives(text: str) -> None:
    result = guard_text(text, surface="property")
    assert result.status in {"redacted", "blocked"}
    assert detect(result.text) == []


@SETTINGS
@given(st.lists(facts, min_size=4, max_size=10), advice, st.integers(min_value=0, max_value=9))
def test_advice_inside_research_is_removed(lines: list[str], bad: str, position: int) -> None:
    lines.insert(position % (len(lines) + 1), bad)
    result = guard_text("\n".join(lines), surface="property")
    assert result.status in {"redacted", "blocked"}
    assert detect(result.text) == []
    assert bad not in result.text


@SETTINGS
@given(st.lists(facts, min_size=1, max_size=8))
def test_facts_pass_unchanged(lines: list[str]) -> None:
    text = "\n".join(lines)
    result = guard_text(text, surface="property")
    assert result.status == "passed"
    assert result.text == text


@SETTINGS
@given(st.one_of(advice, facts, st.text(max_size=400)))
def test_guard_is_idempotent_and_total(text: str) -> None:
    first = guard_text(text, surface="property")
    second = guard_text(str(first.text), surface="property")
    assert str(second.text) == str(first.text)
    assert detect(first.text) == []


@SETTINGS
@given(advice)
def test_blocked_output_never_leaks_original(text: str) -> None:
    result = guard_text(text, surface="property", allow_redaction=False)
    assert result.status == "blocked"
    assert result.text == BLOCKED_MESSAGE
    assert text not in result.text
