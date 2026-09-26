"""Analysis history storage."""
import logging
import re
from datetime import date, datetime, timedelta

from src.modules.automation.agent_catalog import infer_agent_kind
from src.platform.compliance import (
    Feature,
    ensure_guarded,
    guard_title,
    is_feature_enabled,
    sanitize_payload,
)
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)

# agent_name of TradingAgents deep research rows in AnalysisHistory (see agent.py: name = "tradingagents")
TA_AGENT_NAME = "tradingagents"


def save_analysis(
    agent_name: str,
    stock_symbol: str,
    content: str,
    title: str = "",
    raw_data: dict | None = None,
    analysis_date: date | None = None,
) -> bool:
    """
    Save an analysis result.

    - the same day can be overwritten
    - past days can't be overwritten (enforced by a DB constraint)

    Args:
        agent_name: agent name, e.g. "daily_report"
        stock_symbol: stock symbol; "*" means a market-wide analysis
        content: AI analysis text
        title: title
        raw_data: raw data snapshot
        analysis_date: analysis date, today by default

    Returns:
        Whether it was saved
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    # Stored history is shown in the UI and exported to PDF: guard text and drop
    # recommendation fields before persisting (ADR-002).
    content = ensure_guarded(content, surface="analysis_history")
    title = guard_title(title, surface="analysis_history_title") if title else ""

    db = SessionLocal()
    try:
        payload = sanitize_payload(
            to_jsonable(raw_data or {}), guard_strings=True, surface="analysis_history_raw"
        )
        agent_kind = infer_agent_kind(agent_name)

        # Does it exist already?
        existing = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()

        if existing:
            # Update (the same day can be overwritten)
            existing.title = title
            existing.content = content
            existing.raw_data = payload
            existing.agent_kind_snapshot = agent_kind
            logger.info(f"Updated analysis record: {agent_name}/{stock_symbol}/{date_str}")
        else:
            # New
            record = AnalysisHistory(
                agent_name=agent_name,
                stock_symbol=stock_symbol,
                analysis_date=date_str,
                title=title,
                content=content,
                raw_data=payload,
                agent_kind_snapshot=agent_kind,
            )
            db.add(record)
            logger.info(f"Added analysis record: {agent_name}/{stock_symbol}/{date_str}")

        db.commit()
        return True

    except Exception as e:
        logger.error(f"Failed to save analysis record: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_analysis(
    agent_name: str,
    stock_symbol: str,
    analysis_date: date | None = None,
) -> AnalysisHistory | None:
    """
    Get an analysis result.

    Args:
        agent_name: agent name
        stock_symbol: stock symbol
        analysis_date: analysis date, today by default

    Returns:
        The record, or None
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()
    finally:
        db.close()


def get_latest_analysis(
    agent_name: str,
    stock_symbol: str,
    before_date: date | None = None,
) -> AnalysisHistory | None:
    """
    Get the most recent analysis (for yesterday's or earlier analyses).

    Args:
        agent_name: agent name
        stock_symbol: stock symbol
        before_date: most recent record before this date, today by default

    Returns:
        The record, or None
    """
    if before_date is None:
        before_date = date.today()

    date_str = before_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date < date_str,
        ).order_by(AnalysisHistory.analysis_date.desc()).first()
    finally:
        db.close()


def get_analysis_history(
    agent_name: str,
    stock_symbol: str | None = None,
    limit: int = 30,
) -> list[AnalysisHistory]:
    """
    List analysis history.

    Args:
        agent_name: agent name
        stock_symbol: stock symbol; None means all
        limit: maximum number of rows

    Returns:
        Records, newest date first
    """
    db = SessionLocal()
    try:
        query = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
        )

        if stock_symbol:
            query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

        return query.order_by(AnalysisHistory.analysis_date.desc()).limit(limit).all()
    finally:
        db.close()


def get_latest_ta_verdict_row(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> AnalysisHistory | None:
    """The most recent TradingAgents deep research row for a symbol (including today).

    get_latest_analysis uses ``analysis_date < before_date``, which excludes today,
    so ``before_date = today + 1 day`` is passed to include it.

    Args:
        symbol: stock symbol
        within_days: only valid within this many days (older counts as stale; the caller decides)
        today: injectable for tests; date.today() by default

    Returns:
        The most recent AnalysisHistory row, or None.
    """
    if today is None:
        today = date.today()
    # +1 day to include today (get_latest_analysis is strictly less-than)
    return get_latest_analysis(
        TA_AGENT_NAME, symbol, before_date=today + timedelta(days=1)
    )


def _clean_one_liner(text: str, max_chars: int = 120) -> str:
    """Clean a one-sentence summary out of the conclusion text and truncate it to ~max_chars.

    - strip Markdown markers / extra whitespace / control characters
    - take the first sentence (up to the first full stop or newline)
    - truncate long text with an ellipsis
    """
    if not text:
        return ""
    s = str(text)
    # Strip markdown emphasis, heading hashes and link leftovers
    s = re.sub(r"[#*`>\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    # First sentence (full stop, including the CJK full stop, or newline)
    m = re.split(r"[\u3002.!\uff01\n]", s, maxsplit=1)
    head = (m[0] or s).strip()
    candidate = head if len(head) >= 8 else s
    if len(candidate) > max_chars:
        candidate = candidate[:max_chars].rstrip() + "…"
    return candidate


def get_latest_ta_verdict(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> dict | None:
    """A compact version of a symbol's latest TA deep research conclusion (a strong prior for pre-market/close reports).

    Returns only ``{rating, action_label, one_liner, date, age_days}``, never the full text,
    to keep the token budget. Any missing data / parse error -> None (fail-soft, never raises).

    Args:
        symbol: stock symbol
        within_days: only rows within this many days (including today) are used; older returns None
        today: injectable for tests; today by default
    """
    if today is None:
        today = date.today()
    try:
        row = get_latest_ta_verdict_row(symbol, within_days=within_days, today=today)
        if row is None:
            return None

        date_str = str(getattr(row, "analysis_date", "") or "")
        try:
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

        age_days = (today - row_date).days
        if age_days < 0 or age_days > max(1, int(within_days)):
            return None

        raw = getattr(row, "raw_data", None) or {}
        if not isinstance(raw, dict):
            return None
        if not is_feature_enabled(Feature.TRADINGAGENTS_RATING):
            # Research-only: pass a neutral one-line summary forward, never a rating.
            summary = raw.get("research_summary") or getattr(row, "content", "") or ""
            return {
                "one_liner": ensure_guarded(_clean_one_liner(str(summary)), surface="ta_verdict"),
                "date": date_str,
                "age_days": int(age_days),
            }
        sug = raw.get("suggestion") or {}
        if not isinstance(sug, dict):
            sug = {}

        rating = sug.get("rating_raw") or raw.get("rating") or sug.get("action") or "hold"
        action_label = sug.get("action_label") or ""
        reason = sug.get("reason") or getattr(row, "content", "") or ""
        one_liner = _clean_one_liner(reason)

        return {
            "rating": str(rating),
            "action_label": str(action_label),
            "one_liner": one_liner,
            "date": date_str,
            "age_days": int(age_days),
        }
    except Exception as e:  # anything unexpected fails soft
        logger.debug(f"Failed to extract the TA deep research conclusion: {symbol} - {e}")
        return None
