"""Planning pilot: plan-driven orchestration for "give my portfolio a full check".

Deliberately narrow: only one scenario (a full portfolio check). When that intent is recognised it goes plan-driven:
the LLM writes a structured plan (analyse each holding -> portfolio risk -> summary) -> the plan goes to the frontend
as an SSE `plan` event -> steps run one by one (reusing existing tools/LLM) -> a failed step triggers a re-plan (at most once; after that
the failure is included in the summary).

This is a **pilot**: it tests the value of plan-driven flows over fixed ones, without over-generalising. The orchestrator takes the
tool executor (execute_tool) and the SSE stream (stream) as injected dependencies, so unit tests can mock everything.
"""

import json
import logging
import re

from src.platform.compliance.prompt_rules import research_rules

logger = logging.getLogger(__name__)

# Trigger phrases: an explicit match goes plan-driven (a simple heuristic, enough for a pilot)
_PLANNING_TRIGGERS = (
    "full portfolio check",
    "check my portfolio",
    "diagnose my portfolio",
    "portfolio diagnosis",
    "portfolio health check",
    "review my holdings",
    "analyse my holdings",
    "analyze my holdings",
)


def should_use_planning(content: str) -> bool:
    """Whether the user's message asks for a full portfolio check."""
    if not content:
        return False
    text = " ".join(content.lower().split())
    return any(t in text for t in _PLANNING_TRIGGERS)


_PLAN_SYSTEM = (
    "You plan a portfolio research review. Based on the user's holdings, output a "
    "structured plan as JSON only, for example: "
    '{"steps":[{"title":"Review Infosys (INFY)","action":"analyze_stock",'
    '"params":{"symbol":"INFY","market":"IN"}},'
    '{"title":"Portfolio-level risk","action":"portfolio_risk"}]}. '
    "action is one of: analyze_stock (one per holding, params with symbol/market) or "
    "portfolio_risk. Do not include a summary step; the system adds it."
)


def _plan_messages(portfolio_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"Here are my holdings; please write a check plan:\n{portfolio_text}"},
    ]


def _replan_messages(
    portfolio_text: str, failed_title: str, error: str
) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"My holdings:\n{portfolio_text}\n\n"
                f'The step "{failed_title}" in the previous plan failed ({error}). '
                "Please write a new plan that can run (skip or replace the failed step)."
            ),
        },
    ]


def parse_plan(text: str) -> list[dict] | None:
    """Tolerantly parse the list of plan steps from LLM text.

    Handles plain JSON, ```json fences, explanations before/after, a truncated tail and other common messy output.
    Returns None when parsing fails (the caller falls back to the default plan).
    """
    if not text:
        return None

    blob = None
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if m:
        blob = m.group(1)
    else:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            blob = text[min(candidates):]

    if not blob:
        return None

    data = None
    for attempt in (blob, blob[: max(blob.rfind("]"), blob.rfind("}")) + 1]):
        try:
            data = json.loads(attempt)
            break
        except Exception:
            continue
    if data is None:
        return None

    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan")
    if not isinstance(data, list) or not data:
        return None
    return data


def build_default_plan(portfolio_text: str) -> list[dict]:
    """Default plan when the LLM plan is unavailable (portfolio risk only; the system adds the summary)."""
    return [{"title": "Overall portfolio risk", "action": "portfolio_risk"}]


def normalize_steps(steps: list[dict], start_id: int = 1) -> list[dict]:
    """Normalise steps: fill in id/title/action/params/status. Drops summarize (the system summarises automatically)."""
    out = []
    sid = start_id
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = s.get("action") or "portfolio_risk"
        if action == "summarize":
            continue
        out.append(
            {
                "id": sid,
                "title": s.get("title") or f"Step {sid}",
                "action": action,
                "params": s.get("params") or {},
                "status": "pending",
            }
        )
        sid += 1
    return out


def _steps_public(steps: list[dict]) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "status": s["status"]} for s in steps]


async def _publish_plan(stream, steps: list[dict], status: str, current=None) -> None:
    data = {"status": status, "steps": _steps_public(steps)}
    if current is not None:
        data["current"] = current
    await stream.publish("plan", data)


_STEP_SYSTEM = (
    "You are a research analyst writing for an educational service. Using only the data "
    "given, write a concise, evidence-based description (120 words or fewer): trend, "
    "indicator state, descriptive support and resistance, and notable risks.\n"
    + research_rules()
)
_SUMMARY_SYSTEM = (
    "You write an educational portfolio research review from the step results: overall "
    "picture, concentration, main risks and what changed recently. Use short, "
    "evidence-based points. Do not suggest any change to holdings, weights or cash.\n"
    + research_rules()
)


async def _execute_step(db, ai_client, execute_tool, step: dict, portfolio_text: str) -> str:
    """Run one plan step and return its analysis text."""
    action = step["action"]
    if action == "analyze_stock":
        p = step.get("params") or {}
        symbol = p.get("symbol", "")
        market = p.get("market", "IN")
        tech = await execute_tool(db, "get_technical_analysis", {"symbol": symbol, "market": market})
        msgs = [
            {"role": "system", "content": _STEP_SYSTEM},
            {
                "role": "user",
                "content": f"Holding: {step['title']}\nTechnical data:\n{tech}",
            },
        ]
        return await ai_client.chat_multi(msgs, temperature=0.4)

    # portfolio_risk and any other unknown action: treat as portfolio risk
    msgs = [
        {"role": "system", "content": _STEP_SYSTEM},
        {"role": "user", "content": f"Assess the overall risk of this portfolio:\n{portfolio_text}"},
    ]
    return await ai_client.chat_multi(msgs, temperature=0.4)


def _summary_messages(results: list[tuple[str, str]]) -> list[dict]:
    body = "\n\n".join(f"[{title}]\n{res}" for title, res in results)
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": f"Here are the results of each check step; please summarise:\n\n{body}"},
    ]


async def run_portfolio_diagnosis(db, stream, ai_client, execute_tool) -> str:
    """Plan-driven "full portfolio check" orchestration; returns the final summary (already streamed over SSE).

    Args:
        db: DB session.
        stream: SSEStream (must support async publish(event, data)).
        ai_client: AI client (chat_multi / chat_stream).
        execute_tool: async (db, name, args) -> str tool executor.
    """
    await stream.publish("plan", {"status": "planning", "steps": []})

    portfolio_text = await execute_tool(db, "get_portfolio", {})

    # 1) Write the plan (fall back to the default plan on failure or unparseable output)
    steps = None
    try:
        raw = await ai_client.chat_multi(_plan_messages(portfolio_text), temperature=0.3)
        steps = parse_plan(raw)
    except Exception:
        logger.warning("Failed to write the check plan; using the default plan", exc_info=True)
    if not steps:
        steps = build_default_plan(portfolio_text)
    steps = normalize_steps(steps)
    if not steps:
        steps = normalize_steps(build_default_plan(portfolio_text))

    await _publish_plan(stream, steps, status="running")

    # 2) Run step by step, re-planning on failure (at most once)
    results: list[tuple[str, str]] = []
    replanned = False
    i = 0
    while i < len(steps):
        step = steps[i]
        step["status"] = "running"
        await _publish_plan(stream, steps, status="running", current=step["id"])
        try:
            res = await _execute_step(db, ai_client, execute_tool, step, portfolio_text)
            step["status"] = "done"
            results.append((step["title"], res))
        except Exception as e:  # noqa: BLE001
            if not replanned:
                replanned = True
                logger.info("Step \"%s\" failed; re-planning: %s", step["title"], e)
                try:
                    raw = await ai_client.chat_multi(
                        _replan_messages(portfolio_text, step["title"], str(e)),
                        temperature=0.3,
                    )
                    new_steps = parse_plan(raw)
                except Exception:
                    new_steps = None
                if new_steps:
                    steps = steps[:i] + normalize_steps(new_steps, start_id=step["id"])
                    await _publish_plan(stream, steps, status="running")
                    continue  # retry from the current position with the new plan
            # Already re-planned or re-planning failed: mark it failed and carry the failure into the summary
            step["status"] = "failed"
            results.append((step["title"], f"(this step failed: {e})"))
        await _publish_plan(stream, steps, status="running")
        i += 1

    # 3) Summarise (tokens streamed)
    summary = ""
    try:
        parts: list[str] = []
        async for kind, payload in ai_client.chat_stream(
            _summary_messages(results), temperature=0.4
        ):
            if kind == "token":
                parts.append(payload)
                await stream.publish("token", {"text": payload})
        summary = "".join(parts)
    except Exception as e:  # noqa: BLE001 — a failed streaming summary falls back to non-streaming
        logger.warning("Streaming summary failed; falling back to non-streaming: %s", e)
        try:
            summary = await ai_client.chat_multi(_summary_messages(results), temperature=0.4)
            await stream.publish("token", {"text": summary})
        except Exception:
            summary = "Sorry, the check summary failed."
            await stream.publish("token", {"text": summary})

    await _publish_plan(stream, steps, status="done")
    return summary
