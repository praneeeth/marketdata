"""LLM-as-judge: a scoring framework for semantic dimensions (relevance/groundedness/clarity).

Design constraints:
- the judge model config is **read only from environment variables** (EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY /
  EVAL_JUDGE_MODEL), never from the AI service config in the user's database;
- fixed model + low temperature (temperature=0), so scores are reproducible;
- unit tests inject a mock client and send no real requests; real runs are triggered by the author with
  `EVAL_JUDGE_*=... make eval EVAL_ARGS=--judge`;
- calibrate scores against a manual sample before using them as a gate; the rule assertions (framework.py) always come first.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Scoring dimensions (1-5)
JUDGE_DIMENSIONS = {
    "relevance": "Relevance: does the answer address the user's question",
    "groundedness": "Groundedness: are conclusions based only on the tool data, with no invented prices/indicator values",
    "clarity": "Clarity: is it concise, well structured and clear in its conclusion",
}

JUDGE_SYSTEM_PROMPT = """You are a strict reviewer of an AI research assistant's answers.

You get: the user's question, the tool data available to the assistant, and the assistant's final answer.
Score each dimension from 1 to 5 (5 is best):
- relevance: does the answer address the user's question
- groundedness: are conclusions based only on the tool data; any specific price or indicator value not in the
  tool data counts as invented, 2 at most; saying plainly that a tool failed deserves a high score
- clarity: is it concise, well structured, with a clear view

Output only JSON, with no other text:
{"relevance": 1-5, "groundedness": 1-5, "clarity": 1-5, "comment": "a one-sentence comment"}"""


@dataclass
class JudgeConfig:
    """Judge model config (fixed model + low temperature)."""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "JudgeConfig | None":
        """Read from environment variables; None if incomplete (the judge step is skipped)."""
        base_url = os.environ.get("EVAL_JUDGE_BASE_URL", "").strip()
        api_key = os.environ.get("EVAL_JUDGE_API_KEY", "").strip()
        model = os.environ.get("EVAL_JUDGE_MODEL", "").strip()
        if not (base_url and api_key and model):
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


@dataclass
class JudgeScore:
    relevance: int
    groundedness: int
    clarity: int
    comment: str = ""

    @property
    def mean(self) -> float:
        return (self.relevance + self.groundedness + self.clarity) / 3


class LLMJudge:
    """Score answers with the fixed judge model. The client can be injected (a mock in tests)."""

    def __init__(self, config: JudgeConfig, client=None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            from src.platform.ai.ai_client import AIClient

            self.client = AIClient(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )

    async def judge(self, question: str, tool_results: list[str], answer: str) -> JudgeScore:
        """Score one (question, tool data, answer)."""
        tool_block = "\n\n".join(tool_results) if tool_results else "(no tools called this round)"
        user_content = (
            f"## User question\n{question}\n\n"
            f"## Tool data\n{tool_block}\n\n"
            f"## Assistant answer\n{answer}"
        )
        raw = await self.client.chat(
            JUDGE_SYSTEM_PROMPT, user_content, temperature=self.config.temperature
        )
        return self.parse_score(raw)

    @staticmethod
    def parse_score(raw: str) -> JudgeScore:
        """Parse the judge output (tolerating a ```json fence); raises ValueError on malformed output."""
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"judge output is not valid JSON: {raw[:200]!r}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"judge output is not a JSON object: {raw[:200]!r}")

        def clamp(key: str) -> int:
            try:
                return max(1, min(5, int(obj.get(key))))
            except (TypeError, ValueError) as e:
                raise ValueError(f"judge output is missing/invalid dimension {key}: {obj!r}") from e

        return JudgeScore(
            relevance=clamp("relevance"),
            groundedness=clamp("groundedness"),
            clarity=clamp("clarity"),
            comment=str(obj.get("comment") or ""),
        )
