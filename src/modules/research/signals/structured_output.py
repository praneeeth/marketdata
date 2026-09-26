from __future__ import annotations

import json


ALLOWED_ACTIONS = {
    "buy",
    "add",
    "reduce",
    "sell",
    "hold",
    "watch",
    "alert",
    "avoid",
}

ACTION_ALIASES = {
    "build": "add",
}


TAG_START = "<!--CANDLEWISE_JSON-->"
TAG_END = "<!--/CANDLEWISE_JSON-->"
# Written before the rename (stored analyses, cached model output); still accepted when reading.
LEGACY_TAG_START = "<!--PANWATCH_JSON-->"
LEGACY_TAG_END = "<!--/PANWATCH_JSON-->"
_TAG_PAIRS = ((TAG_START, TAG_END), (LEGACY_TAG_START, LEGACY_TAG_END))


def _find_tagged_block(
    raw: str, start: str | None, end: str | None
) -> tuple[int, int, int, int] | None:
    """Locate the last tagged block.

    Returns (block start, payload start, payload end, block end). With no explicit tags the
    current tags are tried first, then the legacy ones.
    """
    pairs = ((start, end),) if start is not None and end is not None else _TAG_PAIRS
    for tag_start, tag_end in pairs:
        i = raw.rfind(tag_start)
        if i < 0:
            continue
        j = raw.rfind(tag_end)
        if j < 0 or j <= i:
            continue
        return i, i + len(tag_start), j, j + len(tag_end)
    return None


def try_parse_action_json(text: str) -> dict | None:
    """Parse JSON-only output. Returns dict on success."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Allow fenced code blocks (```json ... ```)
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lstrip().startswith("```"):
            if lines[-1].strip().startswith("```"):
                raw = "\n".join(lines[1:-1]).strip()
        else:
            raw = raw.strip("`").strip()
    # Allow "json" prefix line without code fences.
    # Example:
    # json
    # {"action":"buy", ...}
    lines = raw.splitlines()
    if lines and lines[0].strip().lower() == "json":
        raw = "\n".join(lines[1:]).strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = (obj.get("action") or "").strip().lower()
    if action in ACTION_ALIASES:
        obj["action"] = ACTION_ALIASES[action]
        action = obj["action"]
    if action and action not in ALLOWED_ACTIONS:
        return None
    return obj


def try_extract_tagged_json(
    text: str, *, start: str | None = None, end: str | None = None
) -> dict | None:
    """Extract a tagged JSON object from a larger text.

    Expected format at the end of the response:
    <!--CANDLEWISE_JSON-->
    { ... }
    <!--/CANDLEWISE_JSON-->
    """

    raw = text or ""
    found = _find_tagged_block(raw, start, end)
    if found is None:
        return None
    _, payload_start, payload_end, _ = found
    payload = raw[payload_start:payload_end].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def strip_tagged_json(text: str, *, start: str | None = None, end: str | None = None) -> str:
    """Remove tagged JSON block from text (if present)."""
    raw = text or ""
    found = _find_tagged_block(raw, start, end)
    if found is None:
        return raw
    block_start, _, _, block_end = found
    return (raw[:block_start] + raw[block_end:]).strip()
