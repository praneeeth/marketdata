"""Mask credentials in API responses and ignore masked values on update.

Credentials (API keys, bot tokens, webhook keys, cookies) must never be returned to the
browser in full. Edit forms send the masked value back unchanged; ``merge_config`` and
``keep_unless_masked`` keep the stored secret in that case.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MASK_CHAR = "•"
_SECRET_KEY = re.compile(r"token|key|secret|password|passwd|cookie|credential|auth", re.I)
_NOT_SECRET = frozenset({"chat_id", "keyword", "topic", "server_url", "phones", "webhook_id"})


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 8:
        return MASK_CHAR * 8
    return f"{text[:3]}{MASK_CHAR * 6}{text[-4:]}"


def is_masked(value: object) -> bool:
    return isinstance(value, str) and MASK_CHAR in value


def is_secret_key(key: str) -> bool:
    return key not in _NOT_SECRET and bool(_SECRET_KEY.search(key))


def mask_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy of ``config`` with secret-looking string values masked."""
    out: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if is_secret_key(str(key)) and isinstance(value, str) and value:
            out[key] = mask_secret(value)
        else:
            out[key] = value
    return out


def merge_config(existing: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Apply ``incoming`` over ``existing`` but keep stored secrets sent back masked."""
    stored = dict(existing or {})
    merged: dict[str, Any] = {}
    for key, value in incoming.items():
        if is_masked(value) and key in stored:
            merged[key] = stored[key]
        elif is_masked(value):
            continue
        else:
            merged[key] = value
    return merged


def keep_unless_masked(existing: str | None, incoming: str | None) -> str | None:
    """For single secret fields: a masked (or absent) incoming value keeps the stored one."""
    if incoming is None or is_masked(incoming):
        return existing
    return incoming
