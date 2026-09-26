"""GET /api/market/global: labelled, public, and switchable off."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from marketdata.global_cues import CueSpec, GlobalCue, GlobalCuesService
from marketdata.india.types import DataQuality

from src.platform.marketdata import global_cues_service


class FakeSource:
    name = "yahoo"
    quality = DataQuality.UNOFFICIAL_DELAYED

    def fetch(self, specs: Sequence[CueSpec]) -> list[GlobalCue]:
        return [
            GlobalCue(s.key, s.name, s.group, self.name, self.quality, last=Decimal("1.5"))
            for s in specs
        ]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from src.bootstrap.application import app

    monkeypatch.setenv("GLOBAL_CUES_SOURCE", "yahoo")
    monkeypatch.setattr(global_cues_service, "_service", GlobalCuesService(FakeSource()))
    return TestClient(app)


def _data(resp: Any) -> Any:
    body = resp.json()
    return body["data"] if isinstance(body, dict) and "data" in body else body


def test_global_cues_are_labelled_unofficial(client: TestClient) -> None:
    body = _data(client.get("/api/market/global"))  # no login needed
    assert body["enabled"] is True
    assert body["quality"] == "unofficial_delayed"
    assert body["quality_label"] == "Delayed / unofficial"
    keys = [c["key"] for c in body["cues"]]
    assert keys[:3] == ["SPX", "IXIC", "DJI"]
    assert "USDINR" in keys
    assert body["cues"][0]["last"] == 1.5
    assert body["cues"][0]["change"] is None


def test_global_cues_can_be_switched_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLOBAL_CUES_SOURCE", "off")
    assert _data(client.get("/api/market/global")) == {"enabled": False, "cues": []}


def test_default_service_is_yahoo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLOBAL_CUES_SOURCE", raising=False)
    monkeypatch.setattr(global_cues_service, "_service", None)
    service = global_cues_service.get_global_cues_service()
    assert service is not None
    assert service.source_name == "yahoo"
    assert global_cues_service.get_global_cues_service() is service
