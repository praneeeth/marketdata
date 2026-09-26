"""Shared pytest fixtures.

By default every notification send function is replaced with a no-op so unit tests never send notifications.
Pass --notify to restore real sending (for integration tests).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def pytest_itemcollected(item):
    """Replace node names in pytest -v output with the test function's docstring."""
    doc = (item.function.__doc__ or "").strip().split("\n")[0]
    if doc:
        item._nodeid = f"{item.parent.nodeid}::{doc}"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--notify",
        action="store_true",
        default=False,
        help="enable real notification sending (off by default)",
    )


@pytest.fixture(autouse=True)
def _suppress_notifications(request, monkeypatch):
    """Block notification sending automatically unless --notify is passed."""
    if request.config.getoption("--notify"):
        return

    # Patch only the transport so the compliance guard and disclaimer in
    # NotifierManager.notify_with_result run in every test.
    monkeypatch.setattr(
        "src.platform.notifications.notifier.NotifierManager._deliver",
        AsyncMock(return_value={"success": True, "suppressed": True}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _mock_stock_link_platform(monkeypatch):
    """Stop the stock_link module reading platform settings from the database."""
    monkeypatch.setattr(
        "src.modules.administration.stock_link.get_platform",
        lambda: "nse",
    )


@pytest.fixture(autouse=True)
def _no_live_global_cues(monkeypatch):
    """Global cues would call Yahoo over the network; tests opt in explicitly."""
    monkeypatch.setenv("GLOBAL_CUES_SOURCE", "off")


@pytest.fixture(autouse=True)
def _clear_market_caches():
    """Reset process-wide market data singletons so tests don't leak state into each other."""
    from src.modules.market import brokers
    from src.platform.marketdata import india_bridge
    from src.platform.marketdata.global_cues_service import reset_global_cues_service

    def _clear():
        india_bridge._bridge = None
        brokers._manager = None
        reset_global_cues_service()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True, scope="session")
def _ensure_db_schema():
    """Make sure the real DB engine has its tables.

    A few cases pass SessionLocal straight to async endpoints (read-only queries); in a fresh CI environment
    data/candlewise.db has no tables and fails with 'no such table: stocks'. Tables are created idempotently at session start
    (no side effect when they exist locally), independent of the in-memory databases the cases create themselves.
    """
    import src.platform.persistence.models  # noqa: F401  registers every ORM model on Base.metadata
    from src.platform.persistence.database import Base, engine

    Base.metadata.create_all(engine)
    yield


# ---------------------------------------------------------------------------
# Shared factory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> dict:
    """Simulation account data."""
    return {
        "id": 1,
        "name": "Test account",
        "initial_capital": 100_000.0,
        "current_capital": 100_000.0,
    }


@pytest.fixture
def mock_signal() -> dict:
    """Mock strategy signal."""
    return {
        "strategy": "trend_follow",
        "symbol": "002837",
        "market": "CN",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "trend breaking out upwards",
    }


@pytest.fixture
def recommendations_enabled(monkeypatch):
    """Enable recommendation features, as a future registered-analyst mode might.

    Used only by tests of retained recommendation mechanisms; research-only (the default)
    keeps them disabled.
    """
    from src.platform.compliance.settings import ComplianceSettings

    monkeypatch.setattr(
        ComplianceSettings, "recommendations_publishable", property(lambda self: True)
    )
