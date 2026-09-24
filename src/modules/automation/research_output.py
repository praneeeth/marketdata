"""Research-only output contract shared by the agents (ADR-003).

Agents ask the model for neutral research (bull case, bear case, key risks, descriptive
technical levels, upcoming events, sources) instead of buy/sell/hold calls. This module
loads the prompts, parses the model's structured output, guards every text field and
renders notification text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from src.platform.compliance import ensure_guarded, guard_title
from src.platform.compliance.prompt_rules import PROMPTS_DIR, research_rules

RULES_PLACEHOLDER = "{{RESEARCH_RULES}}"

_MAX_ITEMS = 3
_MAX_LEVELS = 4


def load_research_prompt(name: str) -> str:
    """Read ``prompts/<name>`` and insert the shared research-only rules."""
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return text.replace(RULES_PLACEHOLDER, research_rules())


@dataclass(frozen=True)
class SourceRef:
    title: str
    url: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class ResearchItem:
    symbol: str
    summary: str = ""
    bull_points: tuple[str, ...] = ()
    bear_points: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()
    support: tuple[float, ...] = ()
    resistance: tuple[float, ...] = ()
    upcoming_events: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntradayObservation:
    notable: bool
    headline: str = ""
    observations: tuple[str, ...] = ()
    support: tuple[float, ...] = ()
    resistance: tuple[float, ...] = ()
    key_risks: tuple[str, ...] = ()
    parsed: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _texts(value: object, *, surface: str, limit: int = _MAX_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(ensure_guarded(item.strip()[:400], surface=surface))
        if len(out) >= limit:
            break
    return tuple(out)


def _levels(value: object) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    out: list[float] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int | float):
            out.append(float(item))
        elif isinstance(item, str):
            try:
                out.append(float(item.replace(",", "").replace("₹", "").strip()))
            except ValueError:
                continue
        if len(out) >= _MAX_LEVELS:
            break
    return tuple(v for v in out if v > 0)


def _sources(value: object) -> tuple[SourceRef, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    out: list[SourceRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            SourceRef(
                title=ensure_guarded(title[:300], surface="research_source"),
                url=str(item.get("url") or "").strip()[:500],
                published_at=str(item.get("published_at") or "").strip()[:40],
            )
        )
        if len(out) >= 8:
            break
    return tuple(out)


def parse_research_items(
    structured: Mapping[str, Any] | None, *, allowed_symbols: Sequence[str] | None = None
) -> list[ResearchItem]:
    """Parse ``{"research": [...]}``; unknown symbols are dropped when a list is given."""
    if not isinstance(structured, Mapping):
        return []
    raw_items = structured.get("research")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, str | bytes):
        return []
    allowed = {s.upper() for s in allowed_symbols} if allowed_symbols is not None else None
    items: list[ResearchItem] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        if not symbol or (allowed is not None and symbol.upper() not in allowed):
            continue
        levels = raw.get("technical_levels")
        levels_map: Mapping[str, Any] = levels if isinstance(levels, Mapping) else {}
        items.append(
            ResearchItem(
                symbol=symbol,
                summary=ensure_guarded(str(raw.get("summary") or "")[:600], surface="research"),
                bull_points=_texts(raw.get("bull_points"), surface="research"),
                bear_points=_texts(raw.get("bear_points"), surface="research"),
                key_risks=_texts(raw.get("key_risks"), surface="research"),
                support=_levels(levels_map.get("support")),
                resistance=_levels(levels_map.get("resistance")),
                upcoming_events=_texts(raw.get("upcoming_events"), surface="research", limit=5),
                sources=_sources(raw.get("sources")),
            )
        )
    return items


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _loads_object(text: str) -> dict[str, Any] | None:
    raw = _FENCE.sub("", (text or "").strip()).strip()
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_intraday_observation(text: str) -> IntradayObservation:
    """Parse the intraday monitor's JSON. Unparseable output is not notable."""
    obj = _loads_object(text)
    if obj is None:
        return IntradayObservation(notable=False, parsed=False)
    levels = obj.get("technical_levels")
    levels_map: Mapping[str, Any] = levels if isinstance(levels, Mapping) else {}
    headline = guard_title(str(obj.get("headline") or "")[:200], surface="intraday_headline")
    return IntradayObservation(
        notable=obj.get("notable") is True,
        headline=headline if str(obj.get("headline") or "").strip() else "",
        observations=_texts(obj.get("observations"), surface="intraday"),
        support=_levels(levels_map.get("support")),
        resistance=_levels(levels_map.get("resistance")),
        key_risks=_texts(obj.get("key_risks"), surface="intraday"),
    )


def _fmt_levels(values: Sequence[float]) -> str:
    return ", ".join(f"{v:,.2f}" for v in values)


def render_intraday_observation(
    *,
    name: str,
    symbol: str,
    price: float | None,
    change_pct: float | None,
    observation: IntradayObservation,
) -> str:
    lines = [f"{name} ({symbol})"]
    price_text = f"{price:,.2f}" if isinstance(price, int | float) and price else "N/A"
    change_text = f"{change_pct:+.2f}%" if isinstance(change_pct, int | float) else "N/A"
    lines.append(f"Price: {price_text}  Change: {change_text}")
    if observation.headline:
        lines.append(f"What is happening: {observation.headline}")
    if observation.observations:
        lines.append("Observations:")
        lines.extend(f"- {o}" for o in observation.observations)
    if observation.support or observation.resistance:
        lines.append("Descriptive technical levels:")
        if observation.support:
            lines.append(f"- Recent support: {_fmt_levels(observation.support)}")
        if observation.resistance:
            lines.append(f"- Recent resistance: {_fmt_levels(observation.resistance)}")
    if observation.key_risks:
        lines.append("Key risks:")
        lines.extend(f"- {r}" for r in observation.key_risks)
    if not observation.parsed:
        lines.append("No structured observation was produced for this check.")
    return ensure_guarded("\n".join(lines), surface="intraday")


def research_payload(items: Sequence[ResearchItem]) -> list[dict[str, Any]]:
    return [item.to_payload() for item in items]
