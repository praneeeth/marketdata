"""Unit tests for AI provider model discovery + the temperature fallback in connection tests."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models as _models  # noqa: F401  registers the ORM
from src.platform.ai.ai_client import AIClient
from src.platform.persistence.database import Base
from src.platform.persistence.models import AIModel, AIService


@pytest.fixture
def db():
    """A separate in-memory SQLite session with every table created."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_service_with_model(db) -> AIModel:
    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    m = AIModel(name="m", service_id=svc.id, model="m", is_default=False)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_client() -> AIClient:
    return AIClient(base_url="http://example.test", api_key="k", model="m")


def test_list_models_returns_sorted_ids():
    """list_models calls models.list() and returns the sorted model ids."""
    client = _make_client()

    class _FakeModels:
        async def list(self):
            data = [type("M", (), {"id": "gpt-4o"})(), type("M", (), {"id": "aaa"})()]
            return type("R", (), {"data": data})()

    client.client.models = _FakeModels()
    assert asyncio.run(client.list_models()) == ["aaa", "gpt-4o"]


def test_chat_omits_temperature_when_none():
    """With temperature=None, the field isn't sent to create."""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=None))
    assert "temperature" not in seen


def test_chat_sends_temperature_when_float():
    """A numeric temperature is sent normally."""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=0))
    assert seen["temperature"] == 0


def test_discover_models_returns_list(db, monkeypatch):
    """discover-models uses the provider's credentials to discover and return model ids."""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return ["gpt-4o", "o1"]

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == ["gpt-4o", "o1"]


def test_discover_models_error_maps_to_400(db, monkeypatch):
    """Discovery failure (provider unsupported / network error) returns 400."""
    from fastapi import HTTPException

    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            raise RuntimeError("404 not found")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.discover_models(svc.id, db))
        assert False, "should raise HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_batch_add_skips_duplicates_and_sets_default(db, monkeypatch):
    """Bulk add: skips existing model ids; setting a default clears the others."""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    db.add(AIModel(name="exists", service_id=svc.id, model="dup", is_default=True))
    db.commit()

    body = providers.BatchModelCreate(
        models=[
            providers.BatchModelItem(
                name="", model="dup", is_default=False
            ),  # duplicate, skipped
            providers.BatchModelItem(name="New A", model="new-a", is_default=True),
            providers.BatchModelItem(name="", model="new-b", is_default=False),
        ]
    )
    res = providers.batch_add_models(
        svc.id, body, db
    )  # sync endpoint (threadpool), doesn't block the event loop
    assert res["added"] == 2

    all_models = db.query(AIModel).filter(AIModel.service_id == svc.id).all()
    names = {m.model for m in all_models}
    assert names == {"dup", "new-a", "new-b"}
    # Once new-a is the default, the others (including the original dup) are cleared
    defaults = [m.model for m in all_models if m.is_default]
    assert defaults == ["new-a"]
    # An empty display name falls back to the model id
    assert next(m for m in all_models if m.model == "new-b").name == "new-b"


def test_batch_add_retries_a_transient_sqlite_lock(db, monkeypatch):
    """Bulk model writes retry on a brief SQLite lock instead of holding the request until the DB timeout."""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    body = providers.BatchModelCreate(
        models=[providers.BatchModelItem(model="new-model")]
    )
    original_commit = db.commit
    attempts = 0

    def commit_with_one_transient_lock():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise providers.OperationalError(
                "UPDATE ai_models SET is_default=?",
                {},
                sqlite3.OperationalError("database is locked"),
            )
        return original_commit()

    monkeypatch.setattr(db, "commit", commit_with_one_transient_lock)
    result = providers.batch_add_models(svc.id, body, db)

    assert result == {"added": 1}
    assert attempts == 2
    assert db.query(AIModel).filter(AIModel.model == "new-model").count() == 1


def test_test_model_omits_temperature(db, monkeypatch):
    """The connection test sends no temperature (safe for models without that parameter)."""
    from src.modules.administration.api import providers

    m = _seed_service_with_model(db)
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            seen["temperature"] = temperature
            return "OK"

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.test_model(m.id, db))
    assert res["ok"] is True
    assert seen["temperature"] is None  # no temperature sent


def test_test_model_error_maps_to_400(db, monkeypatch):
    """An error in the test call maps to 400."""
    from fastapi import HTTPException

    from src.modules.administration.api import providers

    m = _seed_service_with_model(db)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.test_model(m.id, db))
        assert False, "should raise HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_discover_models_empty_list(db, monkeypatch):
    """When discovery returns an empty list, the endpoint returns empty models normally."""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return []

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == []
