#!/usr/bin/env python3
"""Agent process evaluation entry point (make eval).

Runs two groups of cases:
1. structured_output parsing (pure rules, no model needed, always runs);
2. the chat tool loop (needs a real model); config is **read only from environment variables**:
   EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL
   (never the AI service config in the user's database; skipped with a note when unset).

Optional --judge: adds LLM-as-judge semantic scores to the chat case answers
(needs EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY / EVAL_JUDGE_MODEL).

As a gate: run this script when prompts/*.txt or a tool schema changes;
a pass rate below the threshold (EVAL_PASS_THRESHOLD, default 0.9) exits non-zero and blocks the commit.

Examples:
    make eval                                   # rule cases only (without a model)
    EVAL_AI_BASE_URL=... EVAL_AI_API_KEY=... EVAL_AI_MODEL=... make eval
    ... make eval EVAL_ARGS="--judge --only quote-1"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Supports running `python tests/eval/run_eval.py` directly
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.cases.chat_cases import CHAT_CASES  # noqa: E402
from tests.eval.cases.structured_cases import (  # noqa: E402
    STRUCTURED_CASES,
    check_structured_case,
)
from tests.eval.framework import ChatEvalRunner, evaluate_case  # noqa: E402
from tests.eval.judge import JudgeConfig, LLMJudge  # noqa: E402


_LOCAL_EVAL_ENV_KEYS = {
    "EVAL_AI_BASE_URL",
    "EVAL_AI_API_KEY",
    "EVAL_AI_MODEL",
    "EVAL_JUDGE_BASE_URL",
    "EVAL_JUDGE_API_KEY",
    "EVAL_JUDGE_MODEL",
}


def load_local_eval_env() -> None:
    """Load the local .env.eval; values already set in the terminal/CI win."""
    env_file = REPO_ROOT / ".env.eval"
    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _LOCAL_EVAL_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _eval_ai_config() -> tuple[str, str, str] | None:
    """Model config for the chat cases (environment variables only; skipped if any is missing)."""
    base_url = os.environ.get("EVAL_AI_BASE_URL", "").strip()
    api_key = os.environ.get("EVAL_AI_API_KEY", "").strip()
    model = os.environ.get("EVAL_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return base_url, api_key, model


def run_structured(only: str | None) -> tuple[int, int]:
    """Run the structured parsing cases; returns (passed, total)."""
    passed = 0
    cases = [c for c in STRUCTURED_CASES if not only or c.id == only]
    print(f"\n=== structured_output parsing cases ({len(cases)}, pure rules) ===")
    for case in cases:
        failures = check_structured_case(case)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
    return passed, len(cases)


async def run_chat(only: str | None, use_judge: bool) -> tuple[int, int]:
    """Run the chat tool-loop cases; returns (passed, total). Returns (0, 0) without a model."""
    cases = [c for c in CHAT_CASES if not only or c.id == only]
    config = _eval_ai_config()
    if config is None:
        print(
            f"\n=== chat tool-loop cases ({len(cases)}): skipped ===\n"
            "  Needs the environment variables EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL\n"
            "  (read only from environment variables, never the AI service config in the database)"
        )
        return 0, 0

    base_url, api_key, model = config
    from src.platform.ai.ai_client import AIClient

    runner = ChatEvalRunner(AIClient(base_url=base_url, api_key=api_key, model=model))

    judge: LLMJudge | None = None
    if use_judge:
        judge_config = JudgeConfig.from_env()
        if judge_config is None:
            print("  [WARN] --judge needs the EVAL_JUDGE_* environment variables; skipping judge scores this time")
        else:
            judge = LLMJudge(judge_config)

    passed = 0
    print(f"\n=== chat tool-loop cases ({len(cases)}, model: {model}) ===")
    for case in cases:
        result = await runner.run_case(case)
        failures = evaluate_case(case, result)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
        if judge is not None:
            try:
                score = await judge.judge(
                    case.question, list(case.tool_data.values()), result.answer
                )
                print(
                    f"         judge: relevance {score.relevance} groundedness {score.groundedness} "
                    f"clarity {score.clarity} mean {score.mean:.1f} — {score.comment}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"         judge scoring failed: {e}")
    return passed, len(cases)


def main() -> int:
    load_local_eval_env()
    parser = argparse.ArgumentParser(description="Agent process evaluation")
    parser.add_argument("--judge", action="store_true", help="add LLM-as-judge scores to the chat cases")
    parser.add_argument("--only", default="", help="run only the case with this id")
    args = parser.parse_args()
    only = args.only or None

    s_passed, s_total = run_structured(only)
    c_passed, c_total = asyncio.run(run_chat(only, args.judge))

    total = s_total + c_total
    passed = s_passed + c_passed
    if total == 0:
        print("\nNo matching cases")
        return 1

    rate = passed / total
    threshold = float(os.environ.get("EVAL_PASS_THRESHOLD", "0.9"))
    print(f"\n=== Summary ===\n  Passed {passed}/{total} ({rate:.0%}), threshold {threshold:.0%}")
    if rate < threshold:
        print("  ✗ Below the threshold; evaluation failed")
        return 1
    print("  ✓ Evaluation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
