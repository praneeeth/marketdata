"""Unit tests for the three shared-context enhancements.

Covers:
- (4) injecting the latest TradingAgents deep research conclusion (ta_verdict)
- (2) stock strength relative to the index (relative_strength)
- (1) full text of important announcements + more body text for top news (content_fulltext)

Every external fetch (K-lines / DB / full-text endpoint) is monkeypatched,
so the cases need no network and check the fail-soft behaviour.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.modules.research import analysis_history, context_builder
from src.modules.research.context_builder import ContextBuilder
from src.platform.marketdata.models import MarketCode


# --------------------------------------------------------------------------- #
# (4) TradingAgents deep research conclusion
# --------------------------------------------------------------------------- #


def _make_history_row(*, analysis_date: str, suggestion: dict, content: str = ""):
    """Build a fake AnalysisHistory ORM row (only the fields that are read)."""
    return SimpleNamespace(
        agent_name="tradingagents",
        stock_symbol="INFY",
        analysis_date=analysis_date,
        content=content,
        raw_data={"suggestion": suggestion, "rating": suggestion.get("rating_raw")},
    )


def test_ta_verdict_recent_row_injected(monkeypatch, recommendations_enabled):
    """With a TA row within N days, extract the compact conclusion (rating / one-liner / date / age)."""
    today = date.today()
    row = _make_history_row(
        analysis_date=today.strftime("%Y-%m-%d"),
        suggestion={
            "action": "buy",
            "action_label": "Buy",
            "rating_raw": "buy",
            "reason": "fundamentals and technicals aligned, " * 30,  # well over 120 characters, must be truncated
        },
    )

    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: row,
    )

    verdict = analysis_history.get_latest_ta_verdict("INFY", within_days=14)
    assert verdict is not None
    assert verdict["action_label"] == "Buy"
    assert verdict["rating"] == "buy"
    assert verdict["date"] == today.strftime("%Y-%m-%d")
    assert verdict["age_days"] == 0
    # The one-liner must be cleaned and truncated to about 120 characters
    assert isinstance(verdict["one_liner"], str)
    assert 0 < len(verdict["one_liner"]) <= 130


def test_ta_verdict_research_only_has_no_rating(monkeypatch):
    """Research-only: the verdict passed to prompts carries a neutral summary, no rating."""
    today = date.today()
    row = _make_history_row(
        analysis_date=today.strftime("%Y-%m-%d"),
        suggestion={"action": "buy", "action_label": "Buy", "rating_raw": "buy"},
        content="Revenue grew 12% year on year while margins narrowed.",
    )
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: row,
    )
    verdict = analysis_history.get_latest_ta_verdict("INFY", within_days=14)
    assert verdict is not None
    assert set(verdict) == {"one_liner", "date", "age_days"}
    assert verdict["one_liner"].startswith("Revenue grew 12%")


def test_ta_verdict_includes_today(monkeypatch):
    """Today's TA row must be included too (age_days == 0)."""
    today = date.today()
    captured = {}

    def fake_query(agent_name, stock_symbol, before_date=None):
        captured["before_date"] = before_date
        return _make_history_row(
            analysis_date=today.strftime("%Y-%m-%d"),
            suggestion={"action_label": "Overweight", "rating_raw": "overweight", "reason": "volume breakout"},
        )

    monkeypatch.setattr(analysis_history, "get_latest_analysis", fake_query)

    row = analysis_history.get_latest_ta_verdict_row("INFY", within_days=14)
    assert row is not None
    # before_date must be "today + 1 day" to include today
    assert captured["before_date"] == today + timedelta(days=1)


def test_ta_verdict_too_old_returns_none(monkeypatch):
    """Rows older than N days are dropped; returns None."""
    today = date.today()
    old = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    row = _make_history_row(
        analysis_date=old,
        suggestion={"action_label": "Sell", "rating_raw": "sell", "reason": "trend broken"},
    )
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: row,
    )
    verdict = analysis_history.get_latest_ta_verdict("INFY", within_days=14)
    assert verdict is None


def test_ta_verdict_no_row_returns_none(monkeypatch):
    """No TA rows at all: returns None without raising."""
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: None,
    )
    assert analysis_history.get_latest_ta_verdict("INFY") is None


def test_ta_verdict_parse_error_failsoft(monkeypatch):
    """A malformed raw_data (parse error) fails soft and returns None."""
    bad = SimpleNamespace(
        analysis_date=date.today().strftime("%Y-%m-%d"),
        content="x",
        raw_data="not-a-dict",  # makes .get fail
    )
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: bad,
    )
    assert analysis_history.get_latest_ta_verdict("INFY") is None


# --------------------------------------------------------------------------- #
# (2) Strength relative to the index
# --------------------------------------------------------------------------- #


def test_relative_strength_computes_excess():
    """Stock return - index return = excess return, with every field present."""
    cb = ContextBuilder()
    kline_history = {"available": True, "ret_5d": 3.0, "ret_20d": 10.0}
    index_ctx = {"available": True, "ret_5d": 1.0, "ret_20d": 4.0}

    rs = cb._compute_relative_strength(
        market=MarketCode.IN,
        kline_history=kline_history,
        index_ctx=index_ctx,
    )
    assert rs is not None
    assert rs["stock_5d"] == 3.0
    assert rs["index_5d"] == 1.0
    assert rs["excess_5d"] == pytest.approx(2.0)
    assert rs["excess_20d"] == pytest.approx(6.0)
    assert rs["index_label"]  # a non-empty label (e.g. NIFTY 50)


def test_relative_strength_missing_index_returns_none():
    """Missing index data returns None (fail-soft)."""
    cb = ContextBuilder()
    rs = cb._compute_relative_strength(
        market=MarketCode.IN,
        kline_history={"available": True, "ret_5d": 3.0, "ret_20d": 10.0},
        index_ctx={"available": False},
    )
    assert rs is None


def test_relative_strength_missing_stock_returns_none():
    """Missing stock K-lines return None."""
    cb = ContextBuilder()
    rs = cb._compute_relative_strength(
        market=MarketCode.IN,
        kline_history={"available": False},
        index_ctx={"available": True, "ret_5d": 1.0, "ret_20d": 4.0},
    )
    assert rs is None


def test_index_symbol_map_is_nifty_50():
    """India-only: the relative-strength benchmark is NIFTY 50."""
    assert ContextBuilder()._index_for_market(MarketCode.IN) == ("NIFTY 50", "NIFTY 50")


def test_index_returns_cached_once_per_build(monkeypatch):
    """Within one build, each market's index is fetched once (local cache hit, no repeat request)."""
    cb = ContextBuilder()
    calls = {"n": 0}

    def fake_fetch(symbol, market):
        calls["n"] += 1
        return {"available": True, "ret_5d": 1.0, "ret_20d": 2.0}

    monkeypatch.setattr(cb, "_fetch_index_context", fake_fetch)

    first = cb._get_index_context(MarketCode.IN)
    second = cb._get_index_context(MarketCode.IN)
    assert first is second
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# (1) Announcement full text + keeping top news body text
# --------------------------------------------------------------------------- #


def test_news_top_items_retain_more_content():
    """Top news items (with body text) are relaxed to max_chars; the others stay as they are."""
    cb = ContextBuilder()
    long_content = "news body " * 160  # about 1600 characters
    short_content = "brief " * 5  # a short body already truncated by the collection layer
    news = [
        {"title": "Top story", "content": long_content},
        {"title": "Second story", "content": long_content},
        {"title": "Third story", "content": short_content},
    ]
    out = cb._retain_news_content(news, top_k=2, max_chars=800)
    # The top two are relaxed to 800
    assert len(out[0]["content"]) == 800
    assert len(out[1]["content"]) == 800
    # The third isn't in top_k and is kept as is (this function only relaxes the top ones; no extra truncation)
    assert out[2]["content"] == short_content


def test_news_retain_no_content_failsoft():
    """News without a body field doesn't error and stays as it is."""
    cb = ContextBuilder()
    news = [{"title": "Title only"}, {"title": "Title only 2"}]
    out = cb._retain_news_content(news, top_k=2, max_chars=800)
    assert "content" not in out[0]
    assert out[0]["title"] == "Title only"


# --------------------------------------------------------------------------- #
# Integration: build_symbol_contexts writes the three new fields into the payload
# --------------------------------------------------------------------------- #


class _FakePortfolio:
    def get_aggregated_position(self, symbol):
        return None

    accounts: list = []
    total_available_funds = 0
    total_cost = 0


class _FakeContext:
    def __init__(self, watchlist):
        self.watchlist = watchlist
        self.portfolio = _FakePortfolio()


def test_build_symbol_contexts_injects_new_keys(monkeypatch):
    """End to end: the payload carries ta_verdict / relative_strength / events.content_fulltext."""
    stock = SimpleNamespace(symbol="INFY", market=MarketCode.IN, name="Infosys")
    context = _FakeContext([stock])

    events_snapshot = SimpleNamespace(
        days=7,
        items=[
            {
                "title": "Scheme of arrangement for a major restructuring",
                "external_id": "ANX",
                "importance": 3,
                "source": "eastmoney",
                "symbols": ["INFY"],
            }
        ],
    )
    pack = SimpleNamespace(
        quote=object(),
        technical={"trend": "bullish alignment"},
        news=SimpleNamespace(items=[]),
        events=events_snapshot,
    )
    packs = {"INFY": pack}

    # Stock K-lines vs index K-lines, told apart by market.
    def fake_kline(*, symbol, market, lookback_days=120):
        if symbol == "INFY":
            return {"available": True, "ret_5d": 5.0, "ret_20d": 12.0, "trend": "bullish alignment"}
        # Index
        return {"available": True, "ret_5d": 2.0, "ret_20d": 4.0}

    # Stub in the context_builder namespace (it imports these two symbols directly)
    monkeypatch.setattr(context_builder, "build_kline_history_context", fake_kline)
    # (2) The index goes through _fetch_index_context; stub its return directly
    monkeypatch.setattr(
        ContextBuilder, "_fetch_index_context",
        lambda self, symbol, market: {"available": True, "ret_5d": 2.0, "ret_20d": 4.0},
    )
    monkeypatch.setattr(
        context_builder,
        "get_latest_ta_verdict",
        lambda symbol, within_days=14: {
            "rating": "buy",
            "action_label": "Buy",
            "one_liner": "strong fundamentals; staying constructive",
            "date": date.today().strftime("%Y-%m-%d"),
            "age_days": 0,
        },
    )
    # Historical news / snapshot persistence / theme snapshots are stubbed to avoid the DB
    monkeypatch.setattr(ContextBuilder, "_load_history_news", staticmethod(lambda *a, **k: []))
    monkeypatch.setattr(context_builder, "save_stock_context_snapshot", lambda **k: None)
    monkeypatch.setattr(context_builder, "save_news_topic_snapshot", lambda **k: None)

    cb = ContextBuilder()
    result = asyncio.run(
        cb.build_symbol_contexts(
            agent_name="premarket_outlook",
            context=context,
            packs=packs,
            persist_snapshot=False,
        )
    )
    payload = result["symbols"]["INFY"]

    # (4) TA conclusion
    assert payload["ta_verdict"]["action_label"] == "Buy"
    # (2) Relative strength: 5.0 - 2.0 = 3.0
    rs = payload["relative_strength"]
    assert rs is not None
    assert rs["excess_5d"] == pytest.approx(3.0)
    assert rs["excess_20d"] == pytest.approx(8.0)
    assert rs["index_label"] == "NIFTY 50"
    # Announcement full text was a Chinese (Eastmoney) source; events pass through as-is.
    assert "content_fulltext" not in payload["events"][0]


def test_build_symbol_contexts_failsoft_when_index_missing(monkeypatch):
    """A failed index fetch gives relative_strength=None without breaking anything."""
    stock = SimpleNamespace(symbol="TCS", market=MarketCode.IN, name="Tata Consultancy Services")
    context = _FakeContext([stock])
    pack = SimpleNamespace(
        quote=object(),
        technical={"trend": "bullish alignment"},
        news=SimpleNamespace(items=[]),
        events=SimpleNamespace(days=7, items=[]),
    )

    def fake_kline(*, symbol, market, lookback_days=120):
        if symbol == "TCS":
            return {"available": True, "ret_5d": 3.0, "ret_20d": 9.0}
        return {"available": False}  # index unavailable

    monkeypatch.setattr(context_builder, "build_kline_history_context", fake_kline)
    # A failed index fetch -> _fetch_index_context returns available False (deterministic, no network)
    monkeypatch.setattr(
        ContextBuilder, "_fetch_index_context", lambda self, symbol, market: {"available": False}
    )
    monkeypatch.setattr(context_builder, "get_latest_ta_verdict", lambda symbol, within_days=14: None)
    monkeypatch.setattr(ContextBuilder, "_load_history_news", staticmethod(lambda *a, **k: []))

    cb = ContextBuilder()
    result = asyncio.run(
        cb.build_symbol_contexts(
            agent_name="daily_report",
            context=context,
            packs={"TCS": pack},
            persist_snapshot=False,
        )
    )
    payload = result["symbols"]["TCS"]
    assert payload["relative_strength"] is None
    assert payload["ta_verdict"] is None
