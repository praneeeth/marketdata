import base64
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.branding import getenv_compat
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AppSettings
from src.platform.runtime.config import Settings
from src.modules.administration.update_checker import check_update

router = APIRouter()

# Module routers no longer live in a shallow directory at the repo root; the version file must be found from this
# file's absolute location, not the server process's working directory.
VERSION_FILE = Path(__file__).resolve().parents[4] / "VERSION"


def get_app_version() -> str:
    """Get the app version."""
    # Environment variable first
    version = os.getenv("APP_VERSION")
    if version:
        return version

    # Read the VERSION file (several locations supported)
    possible_paths = [Path("VERSION"), VERSION_FILE]
    for path in possible_paths:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
    return "dev"


class SettingUpdate(BaseModel):
    value: str


class SettingResponse(BaseModel):
    key: str
    value: str
    description: str

    class Config:
        from_attributes = True


# Setting descriptions
SETTING_DESCRIPTIONS = {
    "http_proxy": "HTTP proxy address (once set, all outbound requests including quotes/news/AI/notifications use it)",
    "notify_quiet_hours": "Notification quiet hours (HH:MM-HH:MM; empty = off)",
    "notify_retry_attempts": "Notification retry attempts (not counting the first)",
    "notify_retry_backoff_seconds": "Notification retry backoff in seconds (base)",
    "notify_dedupe_ttl_overrides": "Notification dedupe window overrides (JSON; empty = defaults)",
    "stock_link_platform": "Stock link platform (quote site opened from a stock symbol): nse, tradingview or google",
    "candlewise_base_url": "Public URL of the app (for analysis detail links in notifications, e.g. https://candlewise.example.com)",
}

SETTING_KEYS = list(SETTING_DESCRIPTIONS.keys())


def _get_env_defaults() -> dict[str, str]:
    """Read the current values from .env / environment variables as defaults."""
    s = Settings()
    return {
        "http_proxy": s.http_proxy,
        "notify_quiet_hours": s.notify_quiet_hours,
        "notify_retry_attempts": str(s.notify_retry_attempts),
        "notify_retry_backoff_seconds": str(s.notify_retry_backoff_seconds),
        "notify_dedupe_ttl_overrides": s.notify_dedupe_ttl_overrides,
        "stock_link_platform": "nse",
        "candlewise_base_url": getenv_compat("BASE_URL"),
    }


@router.get("", response_model=list[SettingResponse])
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(AppSettings).all()
    existing_map = {s.key: s for s in settings}

    env_defaults = _get_env_defaults()

    result = []
    for key in SETTING_KEYS:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        env_val = env_defaults.get(key, "")

        if key not in existing_map:
            s = AppSettings(key=key, value=env_val, description=desc)
            db.add(s)
            result.append(s)
        else:
            s = existing_map[key]
            if not s.description:
                s.description = desc
            result.append(s)
    db.commit()

    return result


AVATAR_KEY = "ui_avatar"  # the DB stores only the file name; the image lives in data/avatars/


def _avatar_dir() -> str:
    d = os.path.join(os.environ.get("DATA_DIR", "./data"), "avatars")
    os.makedirs(d, exist_ok=True)
    return d


@router.get("/avatar")
def get_avatar(db: Session = Depends(get_db)):
    """Read the user avatar: the DB stores the file name, the image is in data/avatars/, returned as a data URL.

    GET /avatar has no GET /{key} of the same name, so no route conflict.
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    fname = (row.value if row and row.value else "").strip()
    if not fname:
        return {"value": ""}
    path = os.path.join(_avatar_dir(), fname)
    if not os.path.isfile(path):
        return {"value": ""}
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {"value": ""}
    mime = "image/png" if fname.lower().endswith(".png") else "image/jpeg"
    return {"value": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"}


@router.put("/avatar")
def set_avatar(update: SettingUpdate, db: Session = Depends(get_db)):
    """Save/clear the user avatar: writes the data URL to data/avatars/avatar.*; the DB stores only the file name.

    Must be registered before /{key} to match first. An empty string clears it (deletes the file and the record).
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    old = (row.value if row else "") or ""
    value = (update.value or "").strip()

    if not value:
        if old:
            try:
                os.remove(os.path.join(_avatar_dir(), old))
            except OSError:
                pass
        if row:
            row.value = ""
        db.commit()
        return {"value": ""}

    if not (value.startswith("data:") and "," in value):
        raise HTTPException(400, "The avatar must be a data URL")
    header, b64 = value.split(",", 1)
    ext = "png" if "image/png" in header else "jpg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(400, "Invalid avatar data")

    fname = f"avatar.{ext}"
    with open(os.path.join(_avatar_dir(), fname), "wb") as f:
        f.write(raw)
    if old and old != fname:  # remove the old file when the extension changes
        try:
            os.remove(os.path.join(_avatar_dir(), old))
        except OSError:
            pass
    if not row:
        row = AppSettings(key=AVATAR_KEY, value=fname, description="User avatar file name")
        db.add(row)
    else:
        row.value = fname
    db.commit()
    return {"value": fname}


@router.put("/{key}", response_model=SettingResponse)
def update_setting(key: str, update: SettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(AppSettings).filter(AppSettings.key == key).first()
    if not setting:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        setting = AppSettings(key=key, value=update.value, description=desc)
        db.add(setting)
    else:
        setting.value = update.value

    db.commit()
    db.refresh(setting)

    # An http_proxy change is applied to the process env at once, so every httpx client (trust_env=True) uses the new proxy without a restart
    if key == "http_proxy":
        try:
            from server import apply_proxy_env
            apply_proxy_env(update.value)
        except Exception:
            pass

    return setting


@router.get("/version")
def get_version():
    """Get the app version."""
    return {"version": get_app_version()}


@router.get("/update-check")
def get_update_check(db: Session = Depends(get_db)):
    """Check whether a new version is available (cached on the server)."""
    current = get_app_version()
    app_proxy = (
        db.query(AppSettings)
        .filter(AppSettings.key == "http_proxy")
        .first()
    )
    proxy = (app_proxy.value if app_proxy and app_proxy.value else "").strip() or (
        Settings().http_proxy or ""
    )
    result = check_update(current, proxy=proxy)
    err = str(result.get("error") or "").strip()
    if err:
        return {
            "success": False,
            "code": 10061,
            "message": err,
        }
    return result
