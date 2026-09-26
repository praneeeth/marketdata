from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.security.secrets import mask_config, merge_config
from src.platform.persistence.database import get_db
from src.platform.persistence.models import NotifyChannel
from src.platform.notifications.notifier import NotifierManager, CHANNEL_TYPES

router = APIRouter()


class ChannelCreate(BaseModel):
    name: str
    type: str = "telegram"
    config: dict = {}
    enabled: bool = True
    is_default: bool = False


class ChannelUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ChannelResponse(BaseModel):
    id: int
    name: str
    type: str
    config: dict
    enabled: bool
    is_default: bool

    class Config:
        from_attributes = True


def _channel_response(channel: NotifyChannel) -> ChannelResponse:
    response = ChannelResponse.model_validate(channel)
    response.config = mask_config(channel.config or {})
    return response


@router.get("", response_model=list[ChannelResponse])
def list_channels(db: Session = Depends(get_db)):
    return [_channel_response(c) for c in db.query(NotifyChannel).order_by(NotifyChannel.id).all()]


@router.get("/types")
def list_channel_types():
    """Supported channel types and their fields."""
    return CHANNEL_TYPES


@router.post("", response_model=ChannelResponse)
def create_channel(body: ChannelCreate, db: Session = Depends(get_db)):
    if body.is_default:
        db.query(NotifyChannel).update({"is_default": False})
    channel = NotifyChannel(**body.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _channel_response(channel)


@router.put("/{channel_id}", response_model=ChannelResponse)
def update_channel(channel_id: int, body: ChannelUpdate, db: Session = Depends(get_db)):
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Notification channel not found")

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        db.query(NotifyChannel).update({"is_default": False})

    for key, value in data.items():
        if key == "config" and isinstance(value, dict):
            value = merge_config(channel.config, value)
        setattr(channel, key, value)

    db.commit()
    db.refresh(channel)
    return _channel_response(channel)


@router.delete("/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Notification channel not found")
    db.delete(channel)
    db.commit()
    return {"ok": True}


@router.post("/{channel_id}/test")
async def test_channel(channel_id: int, db: Session = Depends(get_db)):
    """Send a test notification."""
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "Notification channel not found")

    notifier = NotifierManager()
    try:
        notifier.add_channel(channel.type, channel.config or {})
    except Exception as e:
        raise HTTPException(400, f"Invalid channel config: {e}")

    result = await notifier.notify_with_result(
        title="Test notification",
        content="This is a test notification from PanWatch. If you received it, the channel is set up correctly.",
        bypass_quiet_hours=True,
    )

    if result.get("success"):
        return {"ok": True, "message": "Test notification sent"}
    else:
        raise HTTPException(500, f"Notification failed: {result.get('error', 'unknown error')}")
