"""Unit tests for the PAT management API: create (plaintext once) / list / revoke, and MCP rejecting a revoked token.

Fully self-contained: in-memory SQLite + TestClient, no external connections.
"""

import src.modules.administration.api.mcp as mcp_module
import src.modules.administration.api.pats as pats_module  # noqa: F401 (make sure the module imports)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.administration.api import mcp as mcp_router_mod
from src.modules.administration.api import pats as pats_router_mod
from src.platform.persistence.database import Base, get_db


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(pats_router_mod.router, prefix="/api/pats")
    app.include_router(mcp_router_mod.router, prefix="/mcp")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(mcp_module, "SessionLocal", Session)
    return TestClient(app), Session


def test_create_pat_returns_plaintext_once(monkeypatch):
    """Creating a PAT returns the plaintext token (pwmcp_ prefix); the list has no plaintext."""
    client, _ = _setup(monkeypatch)
    resp = client.post("/api/pats", json={"name": "claude-desktop"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("pwmcp_")
    assert data["scopes"] == ["mcp:read"]

    listed = client.get("/api/pats").json()["items"]
    assert len(listed) == 1
    assert "token" not in listed[0]  # the list never returns plaintext
    assert listed[0]["prefix"].startswith("pwmcp_")


def test_create_pat_rejects_unknown_scope(monkeypatch):
    """Creating a PAT rejects an unsupported scope."""
    client, _ = _setup(monkeypatch)
    resp = client.post("/api/pats", json={"name": "x", "scopes": ["mcp:write"]})
    assert resp.status_code == 400


def test_revoke_then_mcp_rejects(monkeypatch):
    """After a PAT is revoked, the MCP endpoint rejects the token at once."""
    client, _ = _setup(monkeypatch)
    created = client.post("/api/pats", json={"name": "temp"}).json()
    token = created["token"]
    pat_id = created["id"]

    # Usable before revoking
    ok = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    # Revoke
    dele = client.delete(f"/api/pats/{pat_id}")
    assert dele.status_code == 200

    # Rejected after revoking
    denied = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 401

    # Marked revoked in the list
    listed = client.get("/api/pats").json()["items"]
    assert listed[0]["revoked"] is True
