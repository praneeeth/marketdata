"""Product identity: name, slug, tagline and the environment variable prefix.

The product was renamed from PanWatch (the upstream project this repository forks) to
Candlewise. Settings that used the old ``PANWATCH_`` prefix still work through
:func:`getenv_compat`, which logs a deprecation warning once per variable.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

PRODUCT_NAME = "Candlewise"
PRODUCT_SLUG = "candlewise"
TAGLINE = "Read the market. Decide for yourself."

ENV_PREFIX = "CANDLEWISE_"
LEGACY_ENV_PREFIX = "PANWATCH_"

REPO_URL = "https://github.com/praneeeth/marketdata"
RELEASES_URL = f"{REPO_URL}/releases"

UPSTREAM_NAME = "PanWatch"
UPSTREAM_URL = "https://github.com/TNT-Likely/PanWatch"

_warned: set[str] = set()


def getenv_compat(suffix: str, default: str = "") -> str:
    """Read ``CANDLEWISE_<suffix>``, falling back to the legacy ``PANWATCH_<suffix>``.

    The new name wins when both are set (even when it is set to an empty string).
    """
    new_name = f"{ENV_PREFIX}{suffix}"
    value = os.environ.get(new_name)
    if value is not None:
        return value
    legacy_name = f"{LEGACY_ENV_PREFIX}{suffix}"
    legacy = os.environ.get(legacy_name)
    if legacy is None:
        return default
    if legacy_name not in _warned:
        _warned.add(legacy_name)
        logger.warning("%s is deprecated; rename it to %s", legacy_name, new_name)
    return legacy
