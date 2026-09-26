"""Process-wide global cues service (world indices, crude, gold, USD/INR).

Shared by the /api/market/global endpoint and the pre-market outlook. The interim source
is free, delayed Yahoo data, labelled "Delayed / unofficial"; ``GLOBAL_CUES_SOURCE=off``
turns it off (owner decision, PLAN decision log 2026-09-25).
"""

from __future__ import annotations

import os
import threading

from marketdata.global_cues import GlobalCuesService, YahooGlobalCues

_service: GlobalCuesService | None = None
_lock = threading.Lock()


def get_global_cues_service() -> GlobalCuesService | None:
    """The configured service, or ``None`` when ``GLOBAL_CUES_SOURCE=off``."""
    global _service
    if os.environ.get("GLOBAL_CUES_SOURCE", "yahoo").strip().lower() == "off":
        return None
    with _lock:
        if _service is None:
            _service = GlobalCuesService(YahooGlobalCues())
        return _service


def reset_global_cues_service() -> None:
    global _service
    with _lock:
        _service = None
