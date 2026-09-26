"""Run a small local context-engineering demo without a model or database."""

from __future__ import annotations

import asyncio

from pan_agent import ContextBudget, ContextEngine, ModelMessage


async def main() -> None:
    messages = [
        ModelMessage(role="system", content="You are a careful research assistant."),
        ModelMessage(role="user", content="The goal is to track portfolio risk; alerts must not be changed." + " fact" * 120),
        ModelMessage(role="assistant", content="Goal noted; next, holdings data needs to be added." + " result" * 120),
        ModelMessage(role="user", content="Please continue analysing the current state."),
    ]
    result = await ContextEngine().prepare(
        messages,
        budget=ContextBudget(
            max_tokens=400,
            soft_limit_tokens=128,
            hard_limit_tokens=256,
            keep_recent_messages=1,
        ),
    )
    print("before:", result.usage_before.model_dump(mode="json"))
    print("after:", result.usage_after.model_dump(mode="json"))
    print("compressed:", result.compressed)
    print("summary:", result.summary.model_dump(mode="json") if result.summary else None)
    print("prepared_messages:", len(result.messages))


if __name__ == "__main__":
    asyncio.run(main())
