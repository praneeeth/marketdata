"""Agent process evaluation framework: case structure, runner and rule assertion engine.

A case = fixed input (question + mock tool data) -> rule assertions:
- the right tools are chosen (required ones called, forbidden ones not, none for chit-chat);
- tool arguments are correct;
- actions stay within the allowlist (only the read-only tools registered in CHAT_TOOLS);
- the answer cites tool results (grounding: key values from the mock data must appear in the answer);
- tool failures degrade gracefully (no invented, ungrounded values).

Rule assertions come first; semantic dimensions (relevance/clarity) are added by the LLM-as-judge in judge.py.
Every production bad case should become a new case once fixed (add it to cases/chat_cases.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.modules.assistant.chat_api import SYSTEM_PROMPT
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS

# Action allowlist: the chat agent may only call these read-only tools
TOOL_WHITELIST = {t["function"]["name"] for t in CHAT_TOOLS}
MAX_TOOL_ROUNDS = 5

# Default return when a case gives no mock data for a tool (simulates a tool failure)
DEFAULT_TOOL_MISSING = "Tool error: the eval case gave no mock data for this tool"


@dataclass
class ChatEvalCase:
    """One chat tool-loop evaluation case."""

    id: str
    question: str
    # tool name -> mock return text (failure cases give "Tool error: ..." text directly)
    tool_data: dict[str, str] = field(default_factory=dict)
    # tools that must be called (subset assertion; order not required)
    expected_tools: tuple[str, ...] = ()
    # tools that must not be called
    forbidden_tools: tuple[str, ...] = ()
    # chit-chat/concept questions: no tool may be called at all
    expect_no_tools: bool = False
    # tool name -> {argument: expected value or check function}; with several calls of one tool, any match passes
    param_checks: dict[str, dict] = field(default_factory=dict)
    # grounding: key values the answer must contain (all must match)
    answer_must_contain: tuple[str, ...] = ()
    # the answer must contain any one of these (e.g. "failed/unable/couldn't" phrasing for failure cases)
    answer_must_contain_any: tuple[str, ...] = ()
    # the answer must not contain these (e.g. invented values when a tool failed)
    answer_must_not_contain: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ChatEvalResult:
    """The process record of one case run."""

    case_id: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    answer: str = ""
    error: str = ""


class ChatEvalRunner:
    """Drive the chat tool loop through one case (tool execution replaced by mock data).

    ai_client must implement `chat_with_tools(messages, tools, temperature) -> message`
    (the same as src.platform.ai.ai_client.AIClient):
    - make eval injects a real AIClient (config from environment variables; see run_eval.py);
    - unit tests inject a scripted fake client that sends no real requests.
    """

    def __init__(self, ai_client, temperature: float = 0.0):
        self.ai_client = ai_client
        # Low temperature for evaluation, to reduce non-determinism
        self.temperature = temperature

    async def run_case(self, case: ChatEvalCase) -> ChatEvalResult:
        result = ChatEvalResult(case_id=case.id)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.question},
        ]
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                msg = await self.ai_client.chat_with_tools(
                    messages, tools=CHAT_TOOLS, temperature=self.temperature
                )
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    result.answer = getattr(msg, "content", "") or ""
                    break

                messages.append({
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    result.tool_calls.append((tc.function.name, args))
                    tool_result = case.tool_data.get(tc.function.name, DEFAULT_TOOL_MISSING)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })
            else:
                result.error = "No answer after the maximum number of tool rounds"
        except Exception as e:  # noqa: BLE001 - record any run error in the evaluation
            result.error = f"Run error: {e}"
        return result


def _param_match(actual, expected) -> bool:
    """Argument assertion: expected may be a value or a check function."""
    if callable(expected):
        try:
            return bool(expected(actual))
        except Exception:
            return False
    return str(actual or "").strip() == str(expected)


def evaluate_case(case: ChatEvalCase, result: ChatEvalResult) -> list[str]:
    """Rule assertions on one run; returns a list of failure reasons (empty = pass)."""
    failures: list[str] = []
    if result.error:
        failures.append(result.error)

    called = [name for name, _ in result.tool_calls]
    called_set = set(called)

    # 1) Action allowlist: calling an unregistered tool fails immediately
    for name in sorted(called_set - TOOL_WHITELIST):
        failures.append(f"Called a tool outside the allowlist: {name}")

    # 2) Tool choice
    if case.expect_no_tools and called:
        failures.append(f"Called tools when none should be called: {called}")
    for name in case.expected_tools:
        if name not in called_set:
            failures.append(f"Missing required tool call: {name}")
    for name in case.forbidden_tools:
        if name in called_set:
            failures.append(f"Called a tool that shouldn't be called: {name}")

    # 3) Tool arguments
    for tool_name, expects in (case.param_checks or {}).items():
        calls = [args for name, args in result.tool_calls if name == tool_name]
        if not calls:
            continue  # a missing call was already reported above
        matched = any(
            all(_param_match(args.get(k), v) for k, v in expects.items())
            for args in calls
        )
        if not matched:
            expect_desc = {k: (v if not callable(v) else "<check function>") for k, v in expects.items()}
            failures.append(f"{tool_name} arguments don't match {expect_desc}; actual {calls}")

    # 4) Grounding / content constraints
    answer = result.answer or ""
    for token in case.answer_must_contain:
        if token not in answer:
            failures.append(f"Answer doesn't cite the tool result: {token!r}")
    if case.answer_must_contain_any and not any(
        token in answer for token in case.answer_must_contain_any
    ):
        failures.append(f"Answer contains none of the expected phrases: {case.answer_must_contain_any}")
    for token in case.answer_must_not_contain:
        if token in answer:
            failures.append(f"Answer contains forbidden content: {token!r}")

    return failures
