from src.modules.research.signals.structured_output import try_parse_action_json


def test_try_parse_action_json_plain_json_prefix() -> None:
    """LLM output parsing: json prefix format."""
    text = '\njson\n{"action":"add","action_label":"Open position","reason":"breakout"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
    assert obj.get("action_label") == "Open position"


def test_try_parse_action_json_fenced_json() -> None:
    """LLM output parsing: code block format."""
    text = '```json\n{"action":"reduce","action_label":"Reduce"}\n```'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "reduce"


def test_try_parse_action_json_action_alias_build_to_add() -> None:
    """LLM output parsing: the build alias maps to add."""
    text = '\njson\n{"action":"build","action_label":"Open position","reason":"breakout"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
