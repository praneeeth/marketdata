"""Self-tests for the eval framework (all mocked, no real requests; runs with make test).

Checks that the runner drives the tool loop and records the process, and that the assertion engine
catches each kind of failure: wrong tool / outside the allowlist / wrong arguments / ungrounded answer / tools called for chit-chat.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.eval.cases.chat_cases import CHAT_CASES
from tests.eval.cases.structured_cases import STRUCTURED_CASES
from tests.eval.framework import (
    ChatEvalCase,
    ChatEvalRunner,
    TOOL_WHITELIST,
    evaluate_case,
)
from tests.eval.judge import JudgeConfig, JudgeScore, LLMJudge


def _msg(content=None, tool_calls=None):
    """Stand-in for the message returned by chat_with_tools."""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tc(id, name, arguments):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=id, function=fn)


class ScriptedAIClient:
    """A fake model that returns scripted messages round by round."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.seen_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, temperature=0.0):
        self.seen_messages.append([dict(m) for m in messages])
        assert self._rounds, "script rounds used up"
        return self._rounds.pop(0)


def _run(runner, case):
    return asyncio.run(runner.run_case(case))


def test_runner_records_tool_loop():
    """Runner: runs a full tool-loop round, records calls and the final answer; all assertions pass."""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "INFY", "market": "IN"}')]),
        _msg(content="Infosys is at 1712.5, up 1.35%."),
    ])
    result = _run(ChatEvalRunner(client), case)

    assert result.tool_calls == [("get_stock_quote", {"symbol": "INFY", "market": "IN"})]
    assert "1712.5" in result.answer
    assert evaluate_case(case, result) == []
    # The mock tool data really was injected into the second round's context
    tool_msgs = [m for m in client.seen_messages[-1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "1712.5" in tool_msgs[0]["content"]


def test_assert_catches_wrong_tool():
    """Assertion engine: looked up the watchlist instead of the quote -> reports a missing required tool."""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_watchlist", "{}")]),
        _msg(content="Your watchlist is..."),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("Missing required tool call: get_stock_quote" in f for f in failures)


def test_assert_catches_whitelist_violation():
    """Assertion engine: calling a tool outside the allowlist (such as a write) fails immediately."""
    case = ChatEvalCase(id="x", question="Place a buy order for me")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "place_order", '{"symbol": "INFY"}')]),
        _msg(content="Order placed"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("outside the allowlist: place_order" in f for f in failures)


def test_assert_catches_wrong_params():
    """Assertion engine: a wrong symbol argument -> reports mismatched arguments."""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "000001"}')]),
        _msg(content="Price 1712.5"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("arguments don't match" in f for f in failures)


def test_assert_catches_ungrounded_answer():
    """Assertion engine: the answer doesn't cite the tool's key value -> ungrounded."""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "INFY"}')]),
        _msg(content="Infosys is a good company."),  # no price cited
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("doesn't cite the tool result" in f for f in failures)


def test_assert_catches_chitchat_tool_call():
    """Assertion engine: tools called during chit-chat -> reports they shouldn't be called."""
    case = next(c for c in CHAT_CASES if c.id == "chitchat-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_portfolio", "{}")]),
        _msg(content="Hello! Your holdings are..."),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("Called tools when none should be called" in f for f in failures)


def test_assert_tool_failure_case():
    """Assertion engine: tool failure case; saying so plainly passes, inventing a price fails."""
    case = next(c for c in CHAT_CASES if c.id == "fail-1")

    honest = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "INFY"}')]),
        _msg(content="Sorry, the quote data failed to load; please try again later."),
    ])
    assert evaluate_case(case, _run(ChatEvalRunner(honest), case)) == []

    fabricating = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "INFY"}')]),
        _msg(content="INFY is at 1712.5."),  # quoting a price after the tool failed = invented
    ])
    failures = evaluate_case(case, _run(ChatEvalRunner(fabricating), case))
    assert any("forbidden content" in f for f in failures)


def test_golden_set_size_and_whitelist():
    """Golden set: at least 30 cases, and every expected_tools entry is in the allowlist."""
    assert len(CHAT_CASES) + len(STRUCTURED_CASES) >= 30
    ids = [c.id for c in CHAT_CASES] + [c.id for c in STRUCTURED_CASES]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for case in CHAT_CASES:
        for name in case.expected_tools:
            assert name in TOOL_WHITELIST, f"{case.id} expects a tool outside the allowlist: {name}"


def test_judge_parse_and_mock_call():
    """Judge: end-to-end scoring with a mock client, tolerating a code fence in the output."""

    class FakeJudgeClient:
        async def chat(self, system_prompt, user_content, temperature=0.0):
            assert "reviewer" in system_prompt
            assert "INFY" in user_content
            return '```json\n{"relevance": 5, "groundedness": 4, "clarity": 5, "comment": "grounded and clear"}\n```'

    config = JudgeConfig(base_url="http://mock", api_key="mock", model="mock-judge")
    judge = LLMJudge(config, client=FakeJudgeClient())
    score = asyncio.run(judge.judge("What is INFY's price?", ["Live quote: price 1712.5"], "Now at 1712.5"))
    assert isinstance(score, JudgeScore)
    assert (score.relevance, score.groundedness, score.clarity) == (5, 4, 5)
    assert abs(score.mean - 14 / 3) < 1e-9


def test_judge_parse_rejects_bad_output():
    """Judge: malformed output (not JSON / missing dimension / out-of-range score) is parsed correctly."""
    with pytest.raises(ValueError):
        LLMJudge.parse_score("I think it's fine")
    with pytest.raises(ValueError):
        LLMJudge.parse_score('{"relevance": 5}')
    # Out-of-range scores are clamped to 1-5
    score = LLMJudge.parse_score(json.dumps({"relevance": 9, "groundedness": 0, "clarity": 3}))
    assert (score.relevance, score.groundedness, score.clarity) == (5, 1, 3)


def test_judge_config_from_env(monkeypatch):
    """Judge: config is read only from environment variables; any missing item returns None (never the database)."""
    for key in ("EVAL_JUDGE_BASE_URL", "EVAL_JUDGE_API_KEY", "EVAL_JUDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert JudgeConfig.from_env() is None

    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://judge")
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "k")
    assert JudgeConfig.from_env() is None  # model still missing
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "judge-model")
    config = JudgeConfig.from_env()
    assert config is not None
    assert config.temperature == 0.0
