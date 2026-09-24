"""Shared research-only rules inserted into every system prompt (ADR-003)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
RULES_FILE = PROMPTS_DIR / "_research_rules.txt"


@lru_cache(maxsize=1)
def research_rules() -> str:
    return RULES_FILE.read_text(encoding="utf-8").strip()
