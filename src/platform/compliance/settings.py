"""Advisory-mode configuration, read once from the environment.

``ADVISORY_MODE`` is environment-only by design (ADR-002): it is not editable from the
UI, and an invalid ``ra_registered`` configuration stops the process instead of silently
degrading.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache


class AdvisoryMode(StrEnum):
    RESEARCH_ONLY = "research_only"
    RA_REGISTERED = "ra_registered"


# SEBI research-analyst registration numbers look like INH000012345 (verify with counsel).
_RA_NUMBER_RE = re.compile(r"^INH\d{9}$")


class ComplianceConfigError(RuntimeError):
    """Raised when the advisory-mode configuration is invalid."""


@dataclass(frozen=True)
class ComplianceSettings:
    mode: AdvisoryMode
    ra_registration_number: str = ""
    ra_name: str = ""
    ra_contact: str = ""
    ra_disclosure_url: str = ""

    @property
    def research_only(self) -> bool:
        return self.mode is AdvisoryMode.RESEARCH_ONLY

    @property
    def recommendations_publishable(self) -> bool:
        """Whether AI recommendations may ever reach users directly.

        Always ``False`` in Phase 1: ``ra_registered`` routes recommendation drafts to the
        review queue, and nothing is released until the Phase 5 review UI exists.
        """
        return False


def load_compliance_settings(env: Mapping[str, str] | None = None) -> ComplianceSettings:
    """Parse and validate settings. Raises :class:`ComplianceConfigError` when invalid."""
    source = os.environ if env is None else env
    raw_mode = (source.get("ADVISORY_MODE") or AdvisoryMode.RESEARCH_ONLY.value).strip().lower()
    try:
        mode = AdvisoryMode(raw_mode)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in AdvisoryMode)
        raise ComplianceConfigError(
            f"ADVISORY_MODE={raw_mode!r} is not valid; expected one of: {allowed}"
        ) from exc

    number = (source.get("RA_REGISTRATION_NUMBER") or "").strip().upper()
    name = (source.get("RA_NAME") or "").strip()
    contact = (source.get("RA_CONTACT") or "").strip()
    disclosure_url = (source.get("RA_DISCLOSURE_URL") or "").strip()

    if mode is AdvisoryMode.RA_REGISTERED:
        problems: list[str] = []
        if not _RA_NUMBER_RE.match(number):
            problems.append("RA_REGISTRATION_NUMBER must look like INH followed by 9 digits")
        if not name:
            problems.append("RA_NAME is required")
        if problems:
            raise ComplianceConfigError(
                "ADVISORY_MODE=ra_registered is misconfigured: " + "; ".join(problems)
            )

    return ComplianceSettings(
        mode=mode,
        ra_registration_number=number,
        ra_name=name,
        ra_contact=contact,
        ra_disclosure_url=disclosure_url,
    )


@lru_cache(maxsize=1)
def get_compliance_settings() -> ComplianceSettings:
    """Process-wide settings. Invalid configuration raises (fail closed)."""
    return load_compliance_settings()


def reset_compliance_settings_cache() -> None:
    """For tests only."""
    get_compliance_settings.cache_clear()
