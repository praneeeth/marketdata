"""Run the advice rules over text and report findings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.platform.compliance.normalize import normalize_for_detection
from src.platform.compliance.rules import RULES, Category, Rule

_VETO_WINDOW = 60


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: Category
    matched: str


def _hits(rule: Rule, normalized: str) -> list[str]:
    if rule.sentence_start:
        match = rule.pattern.match(normalized)
        return [match.group(0)] if match else []
    hits: list[str] = []
    for match in rule.pattern.finditer(normalized):
        if rule.veto_before is not None:
            window = normalized[max(0, match.start() - _VETO_WINDOW) : match.start()]
            if rule.veto_before.search(window):
                continue
        hits.append(match.group(0))
    return hits


def detect_normalized(normalized: str) -> list[Finding]:
    """Findings for text that has already been through :func:`normalize_for_detection`."""
    findings: list[Finding] = []
    for rule in RULES:
        for hit in _hits(rule, normalized):
            findings.append(Finding(rule.rule_id, rule.category, hit))
    return findings


def detect(text: str) -> list[Finding]:
    """Findings for raw text (one sentence or a whole document)."""
    return detect_normalized(normalize_for_detection(text))


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？；;])\s+")


def split_segments(text: str) -> list[str]:
    """Split text into lines and sentences, keeping every character.

    ``"".join(split_segments(t)) == t`` holds for any ``t`` so redaction can rebuild the
    document without disturbing markdown layout.
    """
    segments: list[str] = []
    for line in text.splitlines(keepends=True):
        start = 0
        for match in _SENTENCE_SPLIT.finditer(line):
            end = match.end()
            segments.append(line[start:end])
            start = end
        if start < len(line):
            segments.append(line[start:])
    return segments
