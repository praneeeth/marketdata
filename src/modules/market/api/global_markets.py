"""Global market cues API: world indices, crude, gold and USD/INR, for context only.

Public like ``/api/market/indices`` (it holds no user data). The interim source is free,
delayed Yahoo data, labelled "Delayed / unofficial"; set ``GLOBAL_CUES_SOURCE=off`` to hide
it until a licensed feed replaces it (owner decision, PLAN decision log 2026-09-25).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from marketdata.global_cues import GlobalCue

from src.platform.marketdata.global_cues_service import get_global_cues_service

router = APIRouter()

QUALITY_LABEL = {
    "official_realtime": "Official, real-time",
    "official_delayed": "Official, delayed",
    "unofficial_delayed": "Delayed / unofficial",
}


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _cue(c: GlobalCue) -> dict[str, Any]:
    return {
        "key": c.key,
        "name": c.name,
        "group": c.group,
        "last": _num(c.last),
        "change": _num(c.change),
        "change_pct": _num(c.change_pct),
        "as_of": c.as_of.isoformat() if c.as_of else None,
    }


@router.get("/global")
async def global_markets() -> dict[str, Any]:
    service = get_global_cues_service()
    if service is None:
        return {"enabled": False, "cues": []}
    cues = await asyncio.to_thread(service.cues)
    return {
        "enabled": True,
        "source": service.source_name,
        "quality": service.quality.value,
        "quality_label": QUALITY_LABEL[service.quality.value],
        "cues": [_cue(c) for c in cues],
    }
