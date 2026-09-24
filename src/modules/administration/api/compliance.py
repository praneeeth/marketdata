"""Compliance status and disclaimer acknowledgement API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.administration.api.auth import get_current_user
from src.platform.compliance import (
    DISCLAIMER_VERSION,
    LONG_DISCLAIMER,
    SHORT_DISCLAIMER,
    SIMULATION_LABEL,
    SIMULATION_NOTICE,
    enabled_features,
    get_compliance_settings,
)
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AppSettings

router = APIRouter()

ACK_SETTING_KEY = "disclaimer_ack_version"

DbSession = Annotated[Session, Depends(get_db)]


class AckRequest(BaseModel):
    version: str


@router.get("/status")
def compliance_status() -> dict[str, object]:
    """Public: the login page and footer need the disclaimer before sign-in."""
    settings = get_compliance_settings()
    ra: dict[str, str] | None = None
    if not settings.research_only:
        ra = {
            "registration_number": settings.ra_registration_number,
            "name": settings.ra_name,
            "contact": settings.ra_contact,
            "disclosure_url": settings.ra_disclosure_url,
        }
    return {
        "mode": settings.mode.value,
        "disclaimer": {
            "version": DISCLAIMER_VERSION,
            "short": SHORT_DISCLAIMER,
            "long": LONG_DISCLAIMER,
        },
        "simulation": {"label": SIMULATION_LABEL, "notice": SIMULATION_NOTICE},
        "research_analyst": ra,
        "features": enabled_features(settings),
    }


def _stored_ack(db: Session) -> str:
    row = db.query(AppSettings).filter(AppSettings.key == ACK_SETTING_KEY).first()
    return str(row.value or "") if row else ""


@router.get("/ack", dependencies=[Depends(get_current_user)])
def get_ack(db: DbSession) -> dict[str, object]:
    acknowledged = _stored_ack(db)
    return {
        "acknowledged_version": acknowledged,
        "current_version": DISCLAIMER_VERSION,
        "required": acknowledged != DISCLAIMER_VERSION,
    }


@router.post("/ack", dependencies=[Depends(get_current_user)])
def post_ack(body: AckRequest, db: DbSession) -> dict[str, object]:
    if body.version != DISCLAIMER_VERSION:
        raise HTTPException(status_code=409, detail="Disclaimer version is out of date; reload.")
    query = db.query(AppSettings).filter(AppSettings.key == ACK_SETTING_KEY)
    if query.first() is not None:
        query.update({AppSettings.value: DISCLAIMER_VERSION})
    else:
        db.add(
            AppSettings(
                key=ACK_SETTING_KEY,
                value=DISCLAIMER_VERSION,
                description="Disclaimer version acknowledged by the user",
            )
        )
    db.commit()
    return {"acknowledged_version": DISCLAIMER_VERSION, "required": False}
