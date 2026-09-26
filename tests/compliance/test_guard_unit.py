"""Unit tests for compliance settings, guard internals, disclaimers, streams and audit."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.compliance import (
    BLOCKED_MESSAGE,
    LONG_DISCLAIMER,
    REDACTION_MARKER,
    RESEARCH_ONLY_REPLY,
    SHORT_DISCLAIMER,
    SIMULATION_NOTICE,
    TURN_INSTRUCTION,
    ComplianceConfigError,
    GuardedText,
    audit,
    ensure_guarded,
    guard,
    guard_chat_stream,
    guard_text,
    guard_title,
    is_guarded,
    load_compliance_settings,
    sanitize_payload,
    with_short_disclaimer,
)
from src.platform.compliance.detector import detect
from src.platform.compliance.features import (
    Feature,
    FeatureRestrictedError,
    enabled_features,
    require_feature,
)
from src.platform.compliance.settings import (
    AdvisoryMode,
    get_compliance_settings,
    reset_compliance_settings_cache,
)
from src.platform.compliance.stream import GuardedTokenStream

# ------------------------------------------------------------------ settings


def test_default_mode_is_research_only() -> None:
    settings = load_compliance_settings({})
    assert settings.mode is AdvisoryMode.RESEARCH_ONLY
    assert settings.research_only
    assert not settings.recommendations_publishable


def test_invalid_mode_fails_closed() -> None:
    with pytest.raises(ComplianceConfigError, match="not valid"):
        load_compliance_settings({"ADVISORY_MODE": "advice_please"})


@pytest.mark.parametrize(
    "env",
    [
        {"ADVISORY_MODE": "ra_registered"},
        {"ADVISORY_MODE": "ra_registered", "RA_REGISTRATION_NUMBER": "INH12", "RA_NAME": "X"},
        {"ADVISORY_MODE": "ra_registered", "RA_REGISTRATION_NUMBER": "INH000012345"},
    ],
)
def test_ra_mode_requires_valid_registration(env: dict[str, str]) -> None:
    with pytest.raises(ComplianceConfigError, match="misconfigured"):
        load_compliance_settings(env)


def test_valid_ra_mode() -> None:
    settings = load_compliance_settings(
        {
            "ADVISORY_MODE": "RA_REGISTERED",
            "RA_REGISTRATION_NUMBER": "inh000012345",
            "RA_NAME": "Example Research",
            "RA_CONTACT": "ra@example.com",
            "RA_DISCLOSURE_URL": "https://example.com/disclosures",
        }
    )
    assert settings.mode is AdvisoryMode.RA_REGISTERED
    assert settings.ra_registration_number == "INH000012345"
    assert not settings.research_only
    assert not settings.recommendations_publishable


def test_settings_cache_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISORY_MODE", "research_only")
    reset_compliance_settings_cache()
    try:
        assert get_compliance_settings().research_only
    finally:
        reset_compliance_settings_cache()


def test_require_feature_raises_when_disabled() -> None:
    with pytest.raises(FeatureRestrictedError) as exc:
        require_feature(Feature.SUGGESTION_POOL)
    assert exc.value.feature is Feature.SUGGESTION_POOL
    require_feature(Feature.MCP_SERVER) if enabled_features()[Feature.MCP_SERVER.value] else None


# --------------------------------------------------------------------- guard


def test_guarded_text_cannot_be_forged() -> None:
    with pytest.raises(TypeError):
        GuardedText("buy now")


def test_ensure_guarded_passes_guarded_text_through() -> None:
    first = guard_text("Nifty fell 1%.", surface="t").text
    assert ensure_guarded(first, surface="t") is first
    assert is_guarded(first)
    assert not is_guarded("plain")
    assert guard_text(first, surface="t").text is first


def test_empty_and_none_inputs() -> None:
    assert guard_text(None, surface="t").text == ""
    assert guard_text("", surface="t").status == "passed"
    assert ensure_guarded(None, surface="t") == ""


def test_guard_title_uses_fallback() -> None:
    assert guard_title("Buy Reliance now", surface="t") == guard.DEFAULT_TITLE
    assert guard_title("Market wrap", surface="t") == "Market wrap"
    assert guard_title("Buy now", surface="t", fallback="Update") == "Update"


def test_redaction_keeps_list_prefix_and_layout() -> None:
    text = "\n".join(
        [
            "### Infosys (INFY)",
            "- Revenue grew 9% year on year.",
            "- Margins were stable at 21%.",
            "- Deal wins rose to $2.1bn.",
            "- You should buy the stock.",
            "",
            "https://www.nseindia.com/announcement/123",
        ]
    )
    result = guard_text(text, surface="t")
    assert result.status == "redacted"
    assert f"- {REDACTION_MARKER}" in result.text
    assert "https://www.nseindia.com/announcement/123" in result.text


def test_adjacent_redactions_collapse() -> None:
    lines = [f"Fact {i}: revenue grew {i}%." for i in range(10)]
    text = "\n".join([*lines, "Buy it now. Sell it later."])
    result = guard_text(text, surface="t")
    assert result.status == "redacted"
    assert result.text.count(REDACTION_MARKER) == 1


def test_pattern_spanning_lines_is_blocked() -> None:
    lines = [f"Fact {i}: volumes were steady." for i in range(10)]
    text = "\n".join([*lines, "We think investors", "should buy the stock."])
    result = guard_text(text, surface="t")
    assert result.status in {"redacted", "blocked"}
    assert "should buy" not in result.text


def test_guard_fails_closed_on_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("detector crashed")

    events: list[guard.GuardEvent] = []
    monkeypatch.setattr(guard, "_guard", boom)
    guard.set_audit_sink(events.append)
    try:
        result = guard_text("Nifty closed flat.", surface="t")
    finally:
        guard.set_audit_sink(None)
    assert result.status == "blocked"
    assert result.text == BLOCKED_MESSAGE
    assert events[0].status == "blocked"


def test_audit_sink_failure_does_not_change_outcome() -> None:
    def failing_sink(_event: guard.GuardEvent) -> None:
        raise RuntimeError("database down")

    guard.set_audit_sink(failing_sink)
    try:
        result = guard_text("You should buy Reliance.", surface="t")
    finally:
        guard.set_audit_sink(None)
    assert result.status == "blocked"


def test_audit_event_contents() -> None:
    events: list[guard.GuardEvent] = []
    guard.set_audit_sink(events.append)
    try:
        guard_text("Nifty closed flat.", surface="t")
        guard_text("You should buy Reliance.", surface="notification")
    finally:
        guard.set_audit_sink(None)
    assert len(events) == 1
    assert events[0].surface == "notification"
    assert events[0].original == "You should buy Reliance."
    assert len(events[0].original_sha256) == 64
    assert "D01_you_should_act" in events[0].rule_ids


def test_redaction_threshold_blocks_mostly_advice() -> None:
    text = "Revenue grew 10%. You should buy now. Set a stop loss at 900."
    assert guard_text(text, surface="t").status == "blocked"


def test_fixed_texts_are_guard_clean() -> None:
    for text in (
        SHORT_DISCLAIMER,
        LONG_DISCLAIMER,
        SIMULATION_NOTICE,
        BLOCKED_MESSAGE,
        REDACTION_MARKER,
        RESEARCH_ONLY_REPLY,
    ):
        assert guard_text(text, surface="t").status == "passed", text
    assert RESEARCH_ONLY_REPLY in TURN_INSTRUCTION


# ---------------------------------------------------------------- disclaimer


def test_disclaimer_is_appended_and_never_truncated() -> None:
    out = with_short_disclaimer("Market wrap.")
    assert out.endswith(SHORT_DISCLAIMER)
    long_body = "x" * 5000
    trimmed = with_short_disclaimer(long_body, max_chars=500)
    assert len(trimmed) <= 500
    assert trimmed.endswith(SHORT_DISCLAIMER)
    assert "…" in trimmed
    assert with_short_disclaimer("anything", max_chars=10) == SHORT_DISCLAIMER
    assert with_short_disclaimer("   ") == SHORT_DISCLAIMER


# -------------------------------------------------------------------- stream


def test_token_stream_releases_only_checked_sentences() -> None:
    stream = GuardedTokenStream(surface="t")
    assert stream.feed("Revenue grew 12.5") == []
    assert stream.feed("% this quarter. You should ") == ["Revenue grew 12.5% this quarter. "]
    assert stream.feed("buy") == []
    released = stream.feed(" now.\nMargins held.")
    assert released == [REDACTION_MARKER + "\n"]
    assert stream.flush() == ["Margins held."]
    assert stream.flush() == []
    assert stream.redactions == 1


def test_token_stream_handles_cjk_and_whitespace() -> None:
    stream = GuardedTokenStream(surface="t")
    assert stream.feed("营收增长。") == ["营收增长。"]
    assert stream.feed("   ") == []
    assert stream.flush() == ["   "]


async def _events(items: list[tuple[str, Any]]) -> AsyncIterator[tuple[str, Any]]:
    for item in items:
        yield item


def test_guard_chat_stream_guards_tokens_and_final_message() -> None:
    async def run() -> list[tuple[str, Any]]:
        source = _events(
            [
                ("token", "Nifty fell 1%. "),
                ("token", "Buy the dip"),
                ("token", " now."),
                ("message", {"content": "Nifty fell 1%. Buy the dip now.", "tool_calls": []}),
                ("usage", {"total": 10}),
                ("token", "Trailing text"),
            ]
        )
        return [event async for event in guard_chat_stream(source, surface="t")]

    events = asyncio.run(run())
    tokens = [p for k, p in events if k == "token"]
    assert "Nifty fell 1%. " in tokens
    assert all("Buy the dip" not in t for t in tokens)
    message = next(p for k, p in events if k == "message")
    assert "Buy the dip" not in message["content"]
    assert ("usage", {"total": 10}) in events
    assert tokens[-1] == "Trailing text"


def test_guard_chat_stream_passes_tool_only_messages() -> None:
    async def run() -> list[tuple[str, Any]]:
        source = _events([("message", {"content": "", "tool_calls": [{"id": "1"}]})])
        return [event async for event in guard_chat_stream(source, surface="t")]

    assert asyncio.run(run()) == [("message", {"content": "", "tool_calls": [{"id": "1"}]})]


# ------------------------------------------------------------------ payloads


def test_sanitize_payload_removes_recommendation_fields() -> None:
    payload = {
        "suggestions": {"INFY": {"action": "buy"}},
        "suggestion": {"action": "sell"},
        "rating": "overweight",
        "action": "buy",
        "stop_loss": 900,
        "target_price": 1200,
        "nested": [{"action": "start", "note": "You should buy now."}, ("tuple", 1)],
        "url": "https://example.com/buy-now",
        "research": {"summary": "Revenue grew 10%."},
    }
    clean = sanitize_payload(payload, guard_strings=True, surface="t")
    for key in ("suggestions", "suggestion", "rating", "action", "stop_loss", "target_price"):
        assert key not in clean
    assert clean["nested"][0]["action"] == "start"
    assert clean["nested"][0]["note"] == BLOCKED_MESSAGE
    assert clean["nested"][1] == ("tuple", 1)
    assert clean["url"] == "https://example.com/buy-now"
    assert clean["research"]["summary"] == "Revenue grew 10%."
    assert sanitize_payload("You should buy.") == "You should buy."


# --------------------------------------------------------------------- audit


def test_audit_records_and_purges_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import ComplianceEvent

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr("src.platform.persistence.database.SessionLocal", session_factory)

    audit.install_audit_sink()
    try:
        guard_text("You should buy Reliance.", surface="notification")
    finally:
        guard.set_audit_sink(None)

    session = session_factory()
    rows = session.query(ComplianceEvent).all()
    assert len(rows) == 1
    assert rows[0].surface == "notification"
    assert rows[0].status == "blocked"
    assert rows[0].original_text == "You should buy Reliance."

    rows[0].created_at = datetime.now() - timedelta(days=400)  # noqa: DTZ005 - naive column
    session.commit()
    session.close()
    assert audit.purge_old_events(retention_days=180) == 1
    assert audit.purge_old_events(retention_days=180) == 0


def test_guarded_text_copies_and_pickles_as_plain_str() -> None:
    import copy
    import pickle
    from dataclasses import asdict, dataclass

    guarded = guard_text("Revenue grew 9%.", surface="t").text
    pickled = pickle.loads(pickle.dumps(guarded))  # noqa: S301 - trusted, in-process data
    for clone in (copy.copy(guarded), copy.deepcopy(guarded), pickled):
        assert clone == "Revenue grew 9%."
        assert not is_guarded(clone)

    @dataclass
    class Holder:
        text: str

    assert asdict(Holder(guarded)) == {"text": "Revenue grew 9%."}


def test_abbreviations_do_not_split_sentences_or_leak_vetoes() -> None:
    from src.platform.compliance.detector import split_segments

    assert split_segments("It trades at Rs. 1,250. Next line.") == [
        "It trades at Rs. 1,250. ",
        "Next line.",
    ]
    text = "Revenue rose 11% on strong orders.\nReliance could rally to Rs. 1,250.50."
    result = guard_text(text, surface="t")
    assert result.status in {"redacted", "blocked"}
    assert "rally to" not in result.text


@pytest.mark.parametrize(
    "text",
    [
        "Final trade decision: Buy",
        "FINAL TRANSACTION PROPOSAL: **BUY**",
        "Action: Sell",
        "Cut the position by 70%",
        "Build in three tranches, first tranche 30%",
        "Infosys (INFY): Buy",
    ],
)
def test_english_decision_labels_and_sizing_are_blocked(text: str) -> None:
    """TradingAgents-style decision labels and sizing must not pass in English."""
    assert detect(text)


@pytest.mark.parametrize(
    "text",
    [
        "Price action: consolidation near the 200-day average.",
        "The promoter cut its stake by 2% last quarter.",
        "Q2 results: sell-off deepens",
        "Analysts debate: buy or sell",
        "Price action is neutral near the 50-day average.",
        "Price action: hold above the 200-day average so far.",
    ],
)
def test_descriptive_action_and_corporate_stake_changes_pass(text: str) -> None:
    assert not detect(text)
