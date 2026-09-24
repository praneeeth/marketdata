"""Research-only output contract: prompt loading, parsing and rendering (ADR-003)."""

from __future__ import annotations

from src.modules.automation.research_output import (
    RULES_PLACEHOLDER,
    IntradayObservation,
    load_research_prompt,
    parse_intraday_observation,
    parse_research_items,
    render_intraday_observation,
    research_payload,
)
from src.platform.compliance import BLOCKED_MESSAGE
from src.platform.compliance.prompt_rules import PROMPTS_DIR, research_rules

AGENT_PROMPTS = [
    "daily_report.txt",
    "premarket_outlook.txt",
    "intraday_monitor.txt",
    "news_digest.txt",
    "chart_analyst.txt",
]


def test_every_agent_prompt_includes_the_research_rules() -> None:
    rules = research_rules()
    assert "Never give ratings" in rules
    for name in AGENT_PROMPTS:
        prompt = load_research_prompt(name)
        assert RULES_PLACEHOLDER not in prompt
        assert rules in prompt
        raw = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert "action_label" not in raw
        assert "suggestions" not in raw


def test_parse_research_items_validates_and_guards() -> None:
    structured = {
        "research": [
            {
                "symbol": "INFY",
                "summary": "Revenue grew 9%.",
                "bull_points": ["Deal wins rose.", "You should buy now.", 3, "", "Margins held."],
                "bear_points": ["Attrition rose."],
                "key_risks": "not a list",
                "technical_levels": {
                    "support": [1480, "1,450.5", "bad", True, -1],
                    "resistance": 1600,
                },
                "upcoming_events": ["Results on 16 October"],
                "sources": [
                    {"title": "NSE filing", "url": "https://nse/x", "published_at": "2026-09-23"},
                    {"title": ""},
                    "not a dict",
                ],
            },
            {"symbol": "UNKNOWN", "summary": "Ignored."},
            {"summary": "No symbol."},
            "not a dict",
        ]
    }
    items = parse_research_items(structured, allowed_symbols=["INFY"])
    assert len(items) == 1
    item = items[0]
    assert item.summary == "Revenue grew 9%."
    assert item.bull_points == ("Deal wins rose.", BLOCKED_MESSAGE, "Margins held.")
    assert item.key_risks == ()
    assert item.support == (1480.0, 1450.5)
    assert item.resistance == ()
    assert item.sources[0].title == "NSE filing"
    assert len(item.sources) == 1
    payload = research_payload(items)
    assert payload[0]["symbol"] == "INFY"


def test_parse_research_items_handles_missing_data() -> None:
    assert parse_research_items(None) == []
    assert parse_research_items({"research": "x"}) == []
    assert len(parse_research_items({"research": [{"symbol": "TCS"}]})) == 1


def test_parse_intraday_observation_variants() -> None:
    fenced = (
        '```json\n{"notable": true, "headline": "Volume doubled on a 3% move", '
        '"observations": ["RSI 72"], "technical_levels": {"support": [100]}, '
        '"key_risks": ["Results tomorrow"]}\n```'
    )
    obs = parse_intraday_observation(fenced)
    assert obs.notable
    assert obs.headline == "Volume doubled on a 3% move"
    assert obs.support == (100.0,)
    prefixed = parse_intraday_observation('json\n{"notable": "yes", "headline": ""}')
    assert not prefixed.notable
    assert prefixed.headline == ""
    assert not parse_intraday_observation("no json here").parsed
    assert not parse_intraday_observation("{broken json").parsed
    advice = parse_intraday_observation('{"notable": true, "headline": "Buy now"}')
    assert "Buy" not in advice.headline


def test_render_intraday_observation() -> None:
    obs = IntradayObservation(
        notable=True,
        headline="Price broke above recent resistance",
        observations=("Volume 2x average",),
        support=(1480.0,),
        resistance=(1520.5,),
        key_risks=("Index down 1%",),
    )
    text = render_intraday_observation(
        name="Infosys", symbol="INFY", price=1530.0, change_pct=2.5, observation=obs
    )
    assert "Infosys (INFY)" in text
    assert "+2.50%" in text
    assert "Recent support: 1,480.00" in text
    unparsed = render_intraday_observation(
        name="X",
        symbol="X",
        price=None,
        change_pct=None,
        observation=IntradayObservation(notable=False, parsed=False),
    )
    assert "N/A" in unparsed
    assert "No structured observation" in unparsed
