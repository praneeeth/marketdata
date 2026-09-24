"""Research-only feature gates and the compliance API (PLAN.md Phase 1a, 1c)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.application import app
from src.platform.compliance import (
    DISCLAIMER_VERSION,
    RESTRICTED_CODE,
    SHORT_DISCLAIMER,
    Feature,
    enabled_features,
    is_feature_enabled,
)
from src.platform.compliance.settings import AdvisoryMode, load_compliance_settings

client = TestClient(app)


def test_compliance_status_is_public_and_research_only() -> None:
    response = client.get("/api/compliance/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "research_only"
    assert data["disclaimer"]["short"] == SHORT_DISCLAIMER
    assert data["disclaimer"]["version"] == DISCLAIMER_VERSION
    assert data["research_analyst"] is None
    assert data["simulation"]["label"] == "Simulation"
    assert not any(v for k, v in data["features"].items() if k != Feature.MCP_SERVER.value)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/recommendations/entry-candidates"),
        ("get", "/api/recommendations/strategy-signals"),
        ("get", "/api/suggestions"),
        ("get", "/api/suggestions/600519"),
        ("post", "/api/feedback"),
        ("get", "/api/evaluations/agent-predictions"),
        ("get", "/api/factors/weights"),
        ("post", "/api/paper-trading/scan"),
        ("post", "/api/paper-trading/account/toggle"),
        ("post", "/api/paper-trading/premarket-plan"),
        ("post", "/api/paper-trading/daily-summary"),
        ("post", "/api/insights/add-position-eval"),
    ],
)
def test_recommendation_routes_are_restricted(method: str, url: str) -> None:
    response = client.get(url) if method == "get" else client.post(url, json={})
    assert response.status_code == 403, url
    assert response.json()["message"].startswith(RESTRICTED_CODE)


def test_simulation_history_stays_readable() -> None:
    assert client.get("/api/paper-trading/account").status_code == 200


def test_mcp_and_pats_are_not_mounted_by_default() -> None:
    assert client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).status_code in (
        404,
        405,
    )
    assert client.get("/api/pats").status_code == 404
    assert client.get("/.well-known/oauth-protected-resource").status_code != 200 or (
        "resource" not in client.get("/.well-known/oauth-protected-resource").text
    )


def test_disclaimer_acknowledgement_flow() -> None:
    first = client.get("/api/compliance/ack").json()["data"]
    assert first["current_version"] == DISCLAIMER_VERSION
    conflict = client.post("/api/compliance/ack", json={"version": "old"})
    assert conflict.status_code == 409
    done = client.post("/api/compliance/ack", json={"version": DISCLAIMER_VERSION})
    assert done.status_code == 200
    assert client.get("/api/compliance/ack").json()["data"]["required"] is False
    # Idempotent: acknowledging again updates the stored row.
    assert (
        client.post("/api/compliance/ack", json={"version": DISCLAIMER_VERSION}).status_code == 200
    )


def test_ra_registered_mode_still_withholds_recommendations() -> None:
    settings = load_compliance_settings(
        {"ADVISORY_MODE": "ra_registered", "RA_REGISTRATION_NUMBER": "INH000012345", "RA_NAME": "A"}
    )
    assert settings.mode is AdvisoryMode.RA_REGISTERED
    assert not is_feature_enabled(Feature.SUGGESTION_POOL, settings)
    assert enabled_features(settings)[Feature.ENTRY_CANDIDATES.value] is False


def test_mcp_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ENABLED", "true")
    assert is_feature_enabled(Feature.MCP_SERVER)
    monkeypatch.setenv("MCP_ENABLED", "0")
    assert not is_feature_enabled(Feature.MCP_SERVER)
