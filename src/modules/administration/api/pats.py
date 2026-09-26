"""Personal access token (PAT) management API: lets the owner create/list/revoke PATs for the MCP endpoint.

Mounted under the login-protected (JWT) routes: a PAT can't manage PATs (so a leaked one can't renew or escalate itself);
only a logged-in user can. The plaintext token is returned only once, at creation.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.administration.pat import SCOPE_MCP_READ, generate_pat
from src.platform.persistence.database import get_db
from src.platform.persistence.models import PersonalAccessToken

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_SCOPES = {SCOPE_MCP_READ}


class CreatePatBody(BaseModel):
    name: str = Field("", max_length=100)
    scopes: list[str] | None = None  # default ["mcp:read"]
    expires_in_days: int | None = Field(90, ge=1, le=3650)  # None = never expires


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.isoformat()


def _serialize(row: PersonalAccessToken) -> dict:
    try:
        scopes = json.loads(row.scopes_json or "[]")
    except Exception:
        scopes = []
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "scopes": scopes,
        "expires_at": _iso(row.expires_at),
        "last_used_at": _iso(row.last_used_at),
        "revoked_at": _iso(row.revoked_at),
        "created_at": _iso(row.created_at),
        "revoked": row.revoked_at is not None,
    }


@router.post("")
def create_pat(body: CreatePatBody, db: Session = Depends(get_db)):
    """Create a PAT and return the plaintext token (this one time only)."""
    scopes = body.scopes or [SCOPE_MCP_READ]
    invalid = [s for s in scopes if s not in _ALLOWED_SCOPES]
    if invalid:
        raise HTTPException(400, f"Unsupported scope: {invalid}")

    plaintext, token_hash, prefix = generate_pat()
    expires_at = None
    if body.expires_in_days is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        ).replace(tzinfo=None)

    row = PersonalAccessToken(
        name=(body.name or "").strip(),
        token_hash=token_hash,
        prefix=prefix,
        scopes_json=json.dumps(scopes),
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    result = _serialize(row)
    result["token"] = plaintext  # plaintext is returned only at creation
    return result


@router.get("")
def list_pats(db: Session = Depends(get_db)):
    """List all PATs (without plaintext)."""
    rows = (
        db.query(PersonalAccessToken)
        .order_by(PersonalAccessToken.created_at.desc())
        .all()
    )
    return {"items": [_serialize(r) for r in rows]}


@router.delete("/{pat_id}")
def revoke_pat(pat_id: int, db: Session = Depends(get_db)):
    """Revoke a PAT (soft delete: sets revoked_at; the MCP endpoint rejects the token at once)."""
    row = db.query(PersonalAccessToken).filter(PersonalAccessToken.id == pat_id).first()
    if not row:
        raise HTTPException(404, "PAT not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return {"ok": True, "id": pat_id}
