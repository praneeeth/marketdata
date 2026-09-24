"""Persist non-passing guard events to ``compliance_events`` (admin-only data)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.platform.compliance.guard import GuardEvent, set_audit_sink

logger = logging.getLogger(__name__)

MAX_STORED_ORIGINAL_CHARS = 20_000
RETENTION_DAYS = 180


def record_guard_event(event: GuardEvent) -> None:
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import ComplianceEvent

    db = SessionLocal()
    try:
        db.add(
            ComplianceEvent(
                surface=event.surface[:64],
                status=event.status,
                rule_ids=list(event.rule_ids),
                original_sha256=event.original_sha256,
                original_text=event.original[:MAX_STORED_ORIGINAL_CHARS],
            )
        )
        db.commit()
    finally:
        db.close()


def install_audit_sink() -> None:
    set_audit_sink(record_guard_event)


def purge_old_events(retention_days: int = RETENTION_DAYS) -> int:
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import ComplianceEvent

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = db.query(ComplianceEvent).filter(ComplianceEvent.created_at < cutoff).delete()
        db.commit()
        return int(deleted)
    finally:
        db.close()
