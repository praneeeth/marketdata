"""Tests for the planning pilot (full portfolio check).

The ai_client and tool executor are fully mocked; no network. Covers: intent recognition, tolerant plan parsing,
normal step-by-step progress, re-planning on a failed step, and falling back to the default plan when plan generation fails.
"""

import asyncio

from src.modules.assistant.chat_planner import (
    build_default_plan,
    normalize_steps,
    parse_plan,
    run_portfolio_diagnosis,
    should_use_planning,
)


class FakeStream:
    """A fake SSE stream that records published events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event, data):
        self.events.append((event, data))

    def plan_events(self):
        return [d for e, d in self.events if e == "plan"]

    def tokens(self):
        return "".join(d.get("text", "") for e, d in self.events if e == "token")


class FakeAI:
    """Scripted fake AI: chat_multi pops from a queue (raising exceptions), chat_stream yields fixed tokens."""

    def __init__(self, multi_queue, stream_tokens=("check ", "com", "plete")):
        self.multi_queue = list(multi_queue)
        self.stream_tokens = list(stream_tokens)
        self.multi_calls = 0

    async def chat_multi(self, messages, temperature=0.4):
        self.multi_calls += 1
        item = self.multi_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        for t in self.stream_tokens:
            yield ("token", t)
        yield ("message", {"content": "".join(self.stream_tokens), "tool_calls": []})


def _exec_ok(db, name, args):
    async def _inner():
        return {
            "get_portfolio": "Holdings: Infosys (INFY) 100 shares",
            "get_technical_analysis": "Technicals: bullish",
            "get_stock_suggestions": "Items: Hold",
        }.get(name, "")

    return _inner()


# ── Intent recognition ──────────────────────────────────────────────────────
def test_should_use_planning_hits():
    """A trigger phrase -> plan-driven."""
    assert should_use_planning("Please give my portfolio a full portfolio check")
    assert should_use_planning("Can you check my   Portfolio?")
    assert should_use_planning("run a portfolio health check for me")


def test_should_use_planning_miss():
    """Ordinary questions don't trigger it."""
    assert not should_use_planning("What is Infosys trading at now?")
    assert not should_use_planning("")


# ── Tolerant plan parsing ───────────────────────────────────────────────────
def test_parse_plan_plain_list():
    """A plain JSON list."""
    steps = parse_plan('[{"title":"A","action":"portfolio_risk"}]')
    assert steps and steps[0]["title"] == "A"


def test_parse_plan_dict_with_steps():
    """A dict with a steps field."""
    steps = parse_plan('{"steps":[{"title":"B","action":"analyze_stock"}]}')
    assert steps and steps[0]["action"] == "analyze_stock"


def test_parse_plan_json_fence_with_prose():
    """A ```json fence + explanation before and after."""
    text = 'OK, here is the plan:\n```json\n{"steps":[{"title":"C","action":"portfolio_risk"}]}\n```\nPlease confirm'
    steps = parse_plan(text)
    assert steps and steps[0]["title"] == "C"


def test_parse_plan_malformed_returns_none():
    """Not JSON at all -> None."""
    assert parse_plan("Sorry, I can't write a plan") is None
    assert parse_plan("") is None


def test_normalize_steps_filters_summarize_and_assigns_ids():
    """Normalisation: drops summarize, fills in id/status."""
    steps = normalize_steps(
        [
            {"title": "X", "action": "analyze_stock"},
            {"title": "Summary", "action": "summarize"},
            {"title": "Y", "action": "portfolio_risk"},
        ]
    )
    assert [s["id"] for s in steps] == [1, 2]
    assert all(s["status"] == "pending" for s in steps)
    assert all(s["action"] != "summarize" for s in steps)


# ── Orchestration: normal / re-plan / fallback ──────────────────────────────
def test_run_diagnosis_happy_path():
    """Normal: write the plan -> run each step -> streamed summary; plan events move to done."""
    plan = '{"steps":[{"title":"Overall portfolio risk","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, "risk assessment result"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "check complete"  # streamed tokens joined
    plans = stream.plan_events()
    assert plans[0]["status"] == "planning"
    assert plans[-1]["status"] == "done"
    # Every step is done at the end
    assert all(s["status"] == "done" for s in plans[-1]["steps"])
    assert stream.tokens() == "check complete"


def test_run_diagnosis_replan_on_step_failure():
    """A failed step -> one re-plan -> continue with the new plan."""
    # Initial plan: analyze_stock (fails because get_technical_analysis raises)
    plan = '{"steps":[{"title":"Analyse Infosys","action":"analyze_stock","params":{"symbol":"INFY"}}]}'
    replan = '{"steps":[{"title":"Switch to portfolio risk","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, replan, "portfolio risk result"])
    stream = FakeStream()

    calls = {"tech": 0}

    def _exec(db, name, args):
        async def _inner():
            if name == "get_technical_analysis":
                calls["tech"] += 1
                raise RuntimeError("data source timed out")
            return {
                "get_portfolio": "Holdings: Infosys",
                "get_stock_suggestions": "Items",
            }.get(name, "")

        return _inner()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec))

    assert summary == "check complete"
    # A re-plan happened (plan + replan + step = 3 chat_multi calls)
    assert ai.multi_calls == 3
    # The re-planned step appears in the final plan and is done
    final_steps = stream.plan_events()[-1]["steps"]
    assert any("portfolio risk" in s["title"] and s["status"] == "done" for s in final_steps)


def test_run_diagnosis_degrades_when_plan_generation_fails():
    """Plan generation fails -> fall back to the default plan and still produce a summary."""
    ai = FakeAI(multi_queue=[RuntimeError("LLM is down"), "default risk assessment"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "check complete"
    plans = stream.plan_events()
    assert plans[-1]["status"] == "done"
    # The default plan has a single portfolio risk step
    assert len(plans[-1]["steps"]) == 1


def test_build_default_plan_shape():
    """The default plan is a single portfolio risk step."""
    plan = build_default_plan("holdings text")
    assert plan[0]["action"] == "portfolio_risk"
