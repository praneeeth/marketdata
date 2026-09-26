"""共用 pytest fixtures。

默认情况下所有通知发送函数被替换为 no-op，避免单测误发通知。
传入 --notify 参数可恢复真实发送（用于集成测试）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def pytest_itemcollected(item):
    """用测试函数的中文 docstring 替换 pytest -v 输出中的节点名。"""
    doc = (item.function.__doc__ or "").strip().split("\n")[0]
    if doc:
        item._nodeid = f"{item.parent.nodeid}::{doc}"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--notify",
        action="store_true",
        default=False,
        help="启用真实通知发送（默认关闭）",
    )


@pytest.fixture(autouse=True)
def _suppress_notifications(request, monkeypatch):
    """自动屏蔽通知发送，除非传入 --notify。"""
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
    """避免 stock_link 模块访问数据库读取平台设置。"""
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
    """确保真实 DB 引擎已建表。

    少数用例直接用 SessionLocal 传给 async 接口(只读查询),CI 全新环境的
    data/panwatch.db 无表会报 'no such table: stocks'。这里在会话开始时幂等建表
    (本地已有表则无副作用),与各用例自建的内存库互不影响。
    """
    import src.platform.persistence.models  # noqa: F401  注册所有 ORM 模型到 Base.metadata
    from src.platform.persistence.database import Base, engine

    Base.metadata.create_all(engine)
    yield


# ---------------------------------------------------------------------------
# 共用工厂 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> dict:
    """模拟盘账户数据。"""
    return {
        "id": 1,
        "name": "测试账户",
        "initial_capital": 100_000.0,
        "current_capital": 100_000.0,
    }


@pytest.fixture
def mock_signal() -> dict:
    """模拟策略信号。"""
    return {
        "strategy": "trend_follow",
        "symbol": "002837",
        "market": "CN",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "趋势向上突破",
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
