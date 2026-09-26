from src.modules.automation.intraday_monitor import IntradayMonitorAgent


def test_intraday_monitor_loose_json_parse_with_json_prefix() -> None:
    """Intraday monitor: tolerant parsing of a json prefix."""
    agent = IntradayMonitorAgent()
    text = '\njson\n{"action":"add","action_label":"Open position","signal":"volume breakout","reason":"test"}\n'
    obj = agent._try_parse_loose_json(text)  # noqa: SLF001 - internal helper regression
    assert obj is not None
    assert obj.get("action_label") == "Open position"


def test_intraday_monitor_parse_suggestion_accepts_non_standard_action() -> None:
    """Intraday monitor: non-standard action alias mapping."""
    agent = IntradayMonitorAgent()
    text = '\njson\n{"action":"build","action_label":"Open position","signal":"KDJ golden cross","reason":"test"}\n'
    result = agent._parse_suggestion(text)  # noqa: SLF001 - regression
    assert result["action_label"] == "Open position"
    assert result["signal"] == "KDJ golden cross"
