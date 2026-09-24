"""Disclaimer texts shown in the UI, onboarding, notifications and exports.

Bump ``DISCLAIMER_VERSION`` whenever the wording changes; the UI asks users to
acknowledge the new version.
"""

from __future__ import annotations

DISCLAIMER_VERSION = "2026-09-24.1"

SHORT_DISCLAIMER = (
    "Educational/informational only. Not a SEBI-registered investment adviser or research "
    "analyst. Not investment advice. AI can be wrong. Markets carry risk."
)

LONG_DISCLAIMER = (
    "This service provides educational and informational content only. It is not "
    "registered with SEBI as an investment adviser or research analyst, and nothing it "
    "shows is investment advice or an invitation to trade any security. "
    "Content is generated with the help of AI, may be incomplete, out of date or wrong, "
    "and must not be relied on for investment decisions. Investments in securities "
    "markets are subject to market risk. Do your own research and consider consulting a "
    "SEBI-registered adviser."
)

SIMULATION_LABEL = "Simulation"
SIMULATION_NOTICE = "Simulated trades only. No real orders are placed and no money is at risk."

_SEPARATOR = "\n\n---\n"


def with_short_disclaimer(content: str, *, max_chars: int | None = None) -> str:
    """Append the short disclaimer, truncating ``content`` first so it always fits.

    The disclaimer itself is never truncated.
    """
    suffix = _SEPARATOR + SHORT_DISCLAIMER
    body = content.rstrip()
    if max_chars is not None:
        budget = max_chars - len(suffix)
        if budget <= 0:
            return SHORT_DISCLAIMER
        if len(body) > budget:
            ellipsis = "…"
            body = body[: max(0, budget - len(ellipsis))].rstrip() + ellipsis
    return body + suffix if body else SHORT_DISCLAIMER
