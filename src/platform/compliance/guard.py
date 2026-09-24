"""The output guard: every user-facing AI text passes through :func:`guard_text`.

Contract (ADR-002):

* Fail closed. Any exception inside the guard yields ``blocked``, never the original text.
* Sentence-level redaction first; the whole output is blocked when a title/headline is
  affected, when more than ``MAX_REDACTED_RATIO`` of sentences would be removed, when a
  pattern spans sentence boundaries, or when anything survives redaction.
* The result is a :class:`GuardedText`. Sinks use :func:`ensure_guarded`, which passes a
  ``GuardedText`` through unchanged and guards anything else. Any string operation on a
  ``GuardedText`` returns a plain ``str``, so modified text is re-guarded at the sink.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from src.platform.compliance.detector import Finding, detect, detect_normalized, split_segments
from src.platform.compliance.normalize import normalize_for_detection

logger = logging.getLogger(__name__)

GuardStatus = Literal["passed", "redacted", "blocked"]

MAX_REDACTED_RATIO = 0.3

REDACTION_MARKER = "[Removed: this sentence resembled investment advice.]"
BLOCKED_MESSAGE = (
    "This AI output was withheld because it resembled investment advice. "
    "This service offers research and education only: it does not tell you what to trade, "
    "name price levels to aim for or exit at, or suggest amounts to invest."
)
DEFAULT_TITLE = "Research update"

_GUARD_TOKEN = object()
_LIST_PREFIX = re.compile(r"^(\s*(?:[-*+•·]|\d+[.)]|#{1,6}|>)\s+)")
_URL_ONLY = re.compile(r"^\s*https?://\S+\s*$")


class GuardedText(str):
    """A string that has passed the output guard. Only the guard can create one."""

    __slots__ = ()

    def __new__(cls, value: str, *, _token: object = None) -> GuardedText:
        if _token is not _GUARD_TOKEN:
            raise TypeError("GuardedText can only be created by the compliance guard")
        return super().__new__(cls, value)

    # Copies and pickles become plain ``str`` (fail-safe: sinks re-guard plain strings).
    def __reduce_ex__(self, protocol: object) -> tuple[type[str], tuple[str]]:
        return (str, (str(self),))

    def __reduce__(self) -> tuple[type[str], tuple[str]]:
        return (str, (str(self),))


@dataclass(frozen=True)
class GuardEvent:
    surface: str
    status: GuardStatus
    rule_ids: tuple[str, ...]
    original_sha256: str
    original: str


@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    text: GuardedText
    surface: str
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(sorted({f.rule_id for f in self.findings}))


AuditSink = Callable[[GuardEvent], None]
_audit_sink: AuditSink | None = None


def set_audit_sink(sink: AuditSink | None) -> None:
    """Register where non-passing guard events are recorded (see ``audit.py``)."""
    global _audit_sink
    _audit_sink = sink


def _emit(result: GuardResult, original: str) -> None:
    if result.status == "passed" or _audit_sink is None:
        return
    event = GuardEvent(
        surface=result.surface,
        status=result.status,
        rule_ids=result.rule_ids,
        original_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
        original=original,
    )
    try:
        _audit_sink(event)
    except Exception:
        logger.exception("compliance audit sink failed (guard outcome unchanged)")


def _make(value: str) -> GuardedText:
    return GuardedText(value, _token=_GUARD_TOKEN)


def _redact_segment(segment: str) -> str:
    body = segment.rstrip("\r\n")
    ending = segment[len(body) :]
    trailing_ws = body[len(body.rstrip()) :]
    prefix_match = _LIST_PREFIX.match(body)
    prefix = prefix_match.group(1) if prefix_match else ""
    leading_ws = "" if prefix else body[: len(body) - len(body.lstrip())]
    return f"{leading_ws}{prefix}{REDACTION_MARKER}{trailing_ws}{ending}"


def _collapse_markers(text: str) -> str:
    doubled = re.escape(REDACTION_MARKER) + r"(\s+" + re.escape(REDACTION_MARKER) + r")+"
    return re.sub(doubled, REDACTION_MARKER, text)


def _guard(text: str, *, allow_redaction: bool) -> tuple[GuardStatus, str, list[Finding]]:
    segments = split_segments(text)
    sentence_findings: list[Finding] = []
    redacted: list[str] = []
    redacted_count = 0
    content_count = 0
    for segment in segments:
        if not segment.strip() or _URL_ONLY.match(segment):
            redacted.append(segment)
            continue
        content_count += 1
        found = detect(segment)
        if found:
            sentence_findings.extend(found)
            redacted_count += 1
            redacted.append(_redact_segment(segment))
        else:
            redacted.append(segment)

    whole = detect_normalized(normalize_for_detection(text))
    per_rule_whole = Counter(f.rule_id for f in whole)
    per_rule_sentences = Counter(f.rule_id for f in sentence_findings)
    spans_boundaries = any(
        per_rule_whole[rule] > per_rule_sentences.get(rule, 0) for rule in per_rule_whole
    )
    findings = sentence_findings + [f for f in whole if f.rule_id not in per_rule_sentences]

    if not findings:
        return "passed", text, []
    if not allow_redaction or spans_boundaries:
        return "blocked", "", findings
    if content_count == 0 or redacted_count / content_count > MAX_REDACTED_RATIO:
        return "blocked", "", findings

    candidate = _collapse_markers("".join(redacted))
    if detect(candidate.replace(REDACTION_MARKER, " ")):
        return "blocked", "", findings
    return "redacted", candidate, findings


def guard_text(
    text: str | None,
    *,
    surface: str,
    allow_redaction: bool = True,
    blocked_text: str = BLOCKED_MESSAGE,
) -> GuardResult:
    """Guard one piece of user-facing text. Never raises; never returns unguarded text."""
    original = text or ""
    if isinstance(original, GuardedText):
        return GuardResult("passed", original, surface)
    try:
        status, value, findings = _guard(str(original), allow_redaction=allow_redaction)
    except Exception:
        logger.exception("compliance guard error on surface=%s; output blocked", surface)
        result = GuardResult("blocked", _make(blocked_text), surface)
        _emit(result, original)
        return result
    final = blocked_text if status == "blocked" else value
    result = GuardResult(status, _make(final), surface, tuple(findings))
    _emit(result, original)
    return result


def guard_title(title: str | None, *, surface: str, fallback: str = DEFAULT_TITLE) -> GuardedText:
    """Titles and headlines are never partially redacted: any finding replaces them."""
    return guard_text(title, surface=surface, allow_redaction=False, blocked_text=fallback).text


def ensure_guarded(text: str | None, *, surface: str) -> GuardedText:
    """Return ``text`` if already guarded, otherwise guard it."""
    if isinstance(text, GuardedText):
        return text
    return guard_text(text, surface=surface).text


def is_guarded(text: object) -> bool:
    return isinstance(text, GuardedText)
