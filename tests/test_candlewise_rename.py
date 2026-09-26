"""The PanWatch -> Candlewise rename keeps existing data and settings working."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from src.platform import branding
from src.platform.persistence.legacy_db import adopt_legacy_db

# --- environment variables ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANDLEWISE_BASE_URL", raising=False)
    monkeypatch.delenv("PANWATCH_BASE_URL", raising=False)
    monkeypatch.setattr(branding, "_warned", set())


def test_getenv_compat_prefers_new_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANDLEWISE_BASE_URL", "https://new.example")
    monkeypatch.setenv("PANWATCH_BASE_URL", "https://old.example")
    assert branding.getenv_compat("BASE_URL") == "https://new.example"


def test_getenv_compat_new_empty_value_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANDLEWISE_BASE_URL", "")
    monkeypatch.setenv("PANWATCH_BASE_URL", "https://old.example")
    assert branding.getenv_compat("BASE_URL", "fallback") == ""


def test_getenv_compat_falls_back_to_legacy_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PANWATCH_BASE_URL", "https://old.example")
    with caplog.at_level(logging.WARNING, logger=branding.__name__):
        assert branding.getenv_compat("BASE_URL") == "https://old.example"
        assert branding.getenv_compat("BASE_URL") == "https://old.example"
    warnings = [r for r in caplog.records if "PANWATCH_BASE_URL is deprecated" in r.getMessage()]
    assert len(warnings) == 1


def test_getenv_compat_default_when_unset() -> None:
    assert branding.getenv_compat("BASE_URL", "fallback") == "fallback"


# --- database file -----------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_adopt_legacy_db_renames_file_and_sidecars(tmp_path: Path) -> None:
    _write(tmp_path / "panwatch.db", "main")
    _write(tmp_path / "panwatch.db-wal", "wal")
    _write(tmp_path / "panwatch.db-shm", "shm")
    _write(tmp_path / "panwatch.db.bak.20260101_000000", "backup")

    assert adopt_legacy_db(str(tmp_path)) is True

    assert (tmp_path / "candlewise.db").read_text(encoding="utf-8") == "main"
    assert (tmp_path / "candlewise.db-wal").read_text(encoding="utf-8") == "wal"
    assert (tmp_path / "candlewise.db-shm").read_text(encoding="utf-8") == "shm"
    assert not (tmp_path / "panwatch.db").exists()
    assert not (tmp_path / "panwatch.db-wal").exists()
    assert (tmp_path / "panwatch.db.bak.20260101_000000").exists()
    assert adopt_legacy_db(str(tmp_path)) is False  # idempotent


def test_adopt_legacy_db_keeps_existing_new_db(tmp_path: Path) -> None:
    _write(tmp_path / "candlewise.db", "new")
    _write(tmp_path / "panwatch.db", "old")

    assert adopt_legacy_db(str(tmp_path)) is False
    assert (tmp_path / "candlewise.db").read_text(encoding="utf-8") == "new"
    assert (tmp_path / "panwatch.db").read_text(encoding="utf-8") == "old"


def test_adopt_legacy_db_without_legacy_file(tmp_path: Path) -> None:
    assert adopt_legacy_db(str(tmp_path)) is False
    assert not (tmp_path / "candlewise.db").exists()


def test_adopt_legacy_db_finishes_an_interrupted_rename(tmp_path: Path) -> None:
    """Sidecars already moved, main file not yet: the next start completes the move."""
    _write(tmp_path / "panwatch.db", "main")
    _write(tmp_path / "candlewise.db-wal", "wal")

    assert adopt_legacy_db(str(tmp_path)) is True
    assert (tmp_path / "candlewise.db").read_text(encoding="utf-8") == "main"
    assert (tmp_path / "candlewise.db-wal").read_text(encoding="utf-8") == "wal"


# --- settings migration ------------------------------------------------------------------


def _settings_db(tmp_path: Path, rows: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE app_settings (id INTEGER PRIMARY KEY, key VARCHAR UNIQUE NOT NULL,"
                " value VARCHAR, description VARCHAR)"
            )
        )
        for key, value in rows:
            conn.execute(
                text("INSERT INTO app_settings (key, value, description) VALUES (:k, :v, 'd')"),
                {"k": key, "v": value},
            )
    return engine


def _settings(engine) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(text("SELECT key, value FROM app_settings"))}


def test_m131_moves_the_base_url_setting(tmp_path: Path) -> None:
    from src.platform.persistence.migrations import _m131_candlewise_setting_key

    engine = _settings_db(
        tmp_path, [("panwatch_base_url", "https://me.example"), ("http_proxy", "")]
    )
    with engine.begin() as conn:
        _m131_candlewise_setting_key(conn)
        _m131_candlewise_setting_key(conn)  # re-runnable
    assert _settings(engine) == {"candlewise_base_url": "https://me.example", "http_proxy": ""}
    engine.dispose()


def test_m131_fills_an_empty_new_key(tmp_path: Path) -> None:
    from src.platform.persistence.migrations import _m131_candlewise_setting_key

    engine = _settings_db(
        tmp_path, [("panwatch_base_url", "https://me.example"), ("candlewise_base_url", "")]
    )
    with engine.begin() as conn:
        _m131_candlewise_setting_key(conn)
    assert _settings(engine) == {"candlewise_base_url": "https://me.example"}
    engine.dispose()


def test_m131_keeps_a_set_new_key(tmp_path: Path) -> None:
    from src.platform.persistence.migrations import _m131_candlewise_setting_key

    engine = _settings_db(
        tmp_path,
        [
            ("panwatch_base_url", "https://old.example"),
            ("candlewise_base_url", "https://new.example"),
        ],
    )
    with engine.begin() as conn:
        _m131_candlewise_setting_key(conn)
    assert _settings(engine) == {"candlewise_base_url": "https://new.example"}
    engine.dispose()


def test_m131_skips_missing_table(tmp_path: Path) -> None:
    from src.platform.persistence.migrations import _m131_candlewise_setting_key

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        _m131_candlewise_setting_key(conn)
    engine.dispose()


# --- AI output tags and update check -----------------------------------------------------


@pytest.mark.parametrize("tag", ["CANDLEWISE_JSON", "PANWATCH_JSON"])
def test_tagged_json_accepts_new_and_legacy_tags(tag: str) -> None:
    from src.modules.research.signals.structured_output import (
        strip_tagged_json,
        try_extract_tagged_json,
    )

    content = f'Summary text\n<!--{tag}-->\n{{"items": [1]}}\n<!--/{tag}-->'
    assert try_extract_tagged_json(content) == {"items": [1]}
    assert strip_tagged_json(content) == "Summary text"


def test_prompts_use_the_new_tag() -> None:
    root = Path(__file__).resolve().parents[1] / "prompts"
    for name in ("daily_report.txt", "news_digest.txt", "premarket_outlook.txt"):
        body = (root / name).read_text(encoding="utf-8")
        assert "<!--CANDLEWISE_JSON-->" in body
        assert "PANWATCH" not in body


def test_update_check_is_off_until_a_repo_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.modules.administration.update_checker import check_update

    monkeypatch.delenv("UPDATE_CHECK_DOCKER_REPO", raising=False)
    monkeypatch.delenv("UPDATE_CHECK_DISABLE", raising=False)
    result = check_update("1.0.0")
    assert result["enabled"] is False
    assert result["update_available"] is False
    assert result["release_url"] == branding.RELEASES_URL
    assert "UPDATE_CHECK_DOCKER_REPO" in str(result["error"])
