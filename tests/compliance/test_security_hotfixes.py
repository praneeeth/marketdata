"""Regression tests for the Phase 1 security hotfixes (PLAN.md Q-sec)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from src.platform.security.secrets import (
    MASK_CHAR,
    is_masked,
    is_secret_key,
    keep_unless_masked,
    mask_config,
    mask_secret,
    merge_config,
)
from src.platform.security.static_files import resolve_static_file


@pytest.fixture
def static_tree(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("index")
    (static / "assets" / "app.js").write_text("js")
    (tmp_path / "candlewise.db").write_text("SECRET-DB")
    return static


@pytest.mark.parametrize(
    "request_path",
    ["../candlewise.db", "..\\candlewise.db", "/../candlewise.db", "assets/../../candlewise.db"],
)
def test_static_resolution_never_escapes_root(static_tree: Path, request_path: str) -> None:
    resolved = Path(resolve_static_file(str(static_tree), request_path))
    assert resolved == static_tree.resolve() / "index.html"


def test_static_resolution_serves_real_files(static_tree: Path) -> None:
    resolved = Path(resolve_static_file(str(static_tree), "assets/app.js"))
    assert resolved.read_text() == "js"


def test_static_route_blocks_percent_encoded_traversal(static_tree: Path) -> None:
    app = FastAPI()

    @app.get("/{path:path}")
    async def serve_spa(path: str) -> FileResponse:
        return FileResponse(resolve_static_file(str(static_tree), path))

    client = TestClient(app)
    for url in (
        "/%2e%2e/candlewise.db",
        "/..%2fcandlewise.db",
        "/assets/%2e%2e/%2e%2e/candlewise.db",
    ):
        response = client.get(url)
        assert response.status_code == 200
        assert "SECRET-DB" not in response.text
    assert client.get("/assets/app.js").text == "js"


def test_mask_secret_hides_value() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == MASK_CHAR * 8
    masked = mask_secret("sk-abcdefghijklmnop")
    assert masked.startswith("sk-")
    assert masked.endswith("mnop")
    assert "defghijkl" not in masked
    assert is_masked(masked)
    assert not is_masked("sk-plain")


def test_secret_key_detection() -> None:
    for key in ("bot_token", "api_key", "webhook_key", "secret", "sendkey", "cookie", "app_token"):
        assert is_secret_key(key)
    for key in ("chat_id", "keyword", "topic", "server_url", "webhook_id", "name"):
        assert not is_secret_key(key)


def test_mask_and_merge_config_round_trip() -> None:
    stored = {"bot_token": "123456:ABCDEFGHIJKLMN", "chat_id": "42", "proxy": ""}
    shown = mask_config(stored)
    assert shown["chat_id"] == "42"
    assert shown["bot_token"] != stored["bot_token"]
    # The edit form sends the masked token back unchanged: the stored token is kept.
    assert merge_config(stored, {**shown, "chat_id": "43"}) == {
        "bot_token": stored["bot_token"],
        "chat_id": "43",
        "proxy": "",
    }
    # A newly typed token replaces the stored one.
    assert merge_config(stored, {**shown, "bot_token": "new-token-value"})["bot_token"] == (
        "new-token-value"
    )
    # A masked value for an unknown key is dropped rather than stored.
    assert "cookie" not in merge_config(stored, {"cookie": MASK_CHAR * 8})


def test_keep_unless_masked() -> None:
    assert keep_unless_masked("real", None) == "real"
    assert keep_unless_masked("real", mask_secret("real-secret-value")) == "real"
    assert keep_unless_masked("real", "new") == "new"
