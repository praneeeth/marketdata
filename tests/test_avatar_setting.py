"""Settings page avatar: the image is a file in data/avatars, the DB stores only the file name; read/written as a data URL, clearing deletes the file."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401 (make sure the models are registered on Base)
from src.modules.administration.api import settings as settings_api
from src.platform.persistence.database import Base, get_db

# Any valid base64; the backend writes the bytes to a file and GET reads the same data URL back
_IMG = "data:image/jpeg;base64,AAAA"


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/settings")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_avatar_default_empty(tmp_path, monkeypatch):
    """Without a setting, the avatar is an empty string."""
    c = _client(tmp_path, monkeypatch)
    assert c.get("/settings/avatar").json()["value"] == ""


def test_avatar_saved_as_file_db_stores_filename(tmp_path, monkeypatch):
    """After upload: the image is a file in data/avatars, the DB stores only the file name, GET reads a data URL back."""
    c = _client(tmp_path, monkeypatch)
    r = c.put("/settings/avatar", json={"value": _IMG})
    assert r.status_code == 200, r.text
    # The file is on disk in data/avatars
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == ["avatar.jpg"]
    # The DB stores only the file name (short, not base64)
    assert r.json()["value"] == "avatar.jpg"
    # GET reads the data URL back
    assert c.get("/settings/avatar").json()["value"] == _IMG


def test_avatar_clear_deletes_file(tmp_path, monkeypatch):
    """An empty string clears it: deletes the file + GET returns empty."""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    c.put("/settings/avatar", json={"value": ""})
    assert c.get("/settings/avatar").json()["value"] == ""
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == []


def test_avatar_rejects_non_dataurl(tmp_path, monkeypatch):
    """A non-data-URL avatar value is rejected (400)."""
    c = _client(tmp_path, monkeypatch)
    assert c.put("/settings/avatar", json={"value": "http://x/a.png"}).status_code == 400


def test_avatar_key_not_in_generic_list(tmp_path, monkeypatch):
    """The avatar key doesn't mix into the general settings list."""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    keys = [s["key"] for s in c.get("/settings").json()]
    assert "ui_avatar" not in keys
