"""Adopt the SQLite file left by the PanWatch name so existing data survives the rename."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DB_FILENAME = "candlewise.db"
LEGACY_DB_FILENAME = "panwatch.db"

# SQLite keeps uncommitted/uncheckpointed pages next to the main file; they must move with it.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def adopt_legacy_db(data_dir: str) -> bool:
    """Rename ``panwatch.db`` (and its sidecar files) to ``candlewise.db`` once.

    Does nothing when ``candlewise.db`` already exists or there is no legacy file, so it
    is safe to call on every start. Sidecars move first and the main file last, so an
    interrupted run is completed by the next start. Backups (``panwatch.db.bak.*``) are
    left where they are. Returns True when the main file was renamed.
    """
    new_path = os.path.join(data_dir, DB_FILENAME)
    legacy_path = os.path.join(data_dir, LEGACY_DB_FILENAME)
    if os.path.exists(new_path) or not os.path.isfile(legacy_path):
        return False
    for suffix in _SIDECAR_SUFFIXES:
        src = legacy_path + suffix
        dst = new_path + suffix
        if os.path.exists(src) and not os.path.exists(dst):
            os.rename(src, dst)
    os.rename(legacy_path, new_path)
    logger.info("Renamed the database %s to %s", legacy_path, new_path)
    return True
