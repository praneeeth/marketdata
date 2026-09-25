"""Bridge from the app's ``(symbol, market="IN")`` calls to the India data layer.

Upstream code asks for quotes and daily K-lines by symbol and market code. For ``IN``
those requests are answered from the *current user's* broker connections through
:class:`marketdata.india.service.IndiaMarketData`. Until multi-user auth arrives in
Phase 5 the current user is ``"local"``.

Failures are soft, as upstream callers expect: an empty result plus a
:func:`data_notice` explaining why ("Kite session expired, log in again"). Agents and
the UI use that notice to degrade instead of failing silently.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from marketdata.india import IST, Exchange, InstrumentRef, Interval, Quote
from marketdata.india.service import AllProvidersFailed
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LOCAL_USER = "local"
_EXCHANGES = {"NSE": Exchange.NSE, "BSE": Exchange.BSE}
# Calendar days to request per wanted trading day (weekends and holidays).
_CALENDAR_FACTOR = 1.6


@dataclass(frozen=True)
class DailyBar:
    """Same fields as the upstream ``KlineData`` (``date`` is ``YYYY-MM-DD``)."""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


def parse_symbol(symbol: str) -> InstrumentRef:
    """``"INFY"`` → NSE:INFY, ``"BSE:INFY"`` → BSE:INFY, ``"NIFTY 50"`` → NSE index."""
    text = " ".join(symbol.strip().upper().split())
    exchange = Exchange.NSE
    prefix, sep, rest = text.partition(":")
    if sep and prefix in _EXCHANGES:
        exchange, text = _EXCHANGES[prefix], rest.strip()
    if not text:
        raise ValueError("empty symbol")
    return InstrumentRef(exchange, text)


def display_symbol(ref: InstrumentRef) -> str:
    return ref.tradingsymbol if ref.exchange is Exchange.NSE else ref.display


def _open_db() -> Session:
    from src.platform.persistence.database import SessionLocal

    return SessionLocal()


# ``platform`` must not import ``modules`` (tests/test_architecture_boundaries.py), so the
# broker manager is plugged in at startup by ``src/bootstrap/application.py``.
_manager_source: Callable[[], Any] | None = None


def register_broker_manager(source: Callable[[], Any]) -> None:
    global _manager_source
    _manager_source = source


class IndiaDataNotConfigured(RuntimeError):
    pass


def _manager() -> Any:
    if _manager_source is None:
        raise IndiaDataNotConfigured("broker connections are not wired up in this process")
    return _manager_source()


class IndiaBridge:
    def __init__(
        self,
        *,
        manager_factory: Callable[[], Any] = _manager,
        db_factory: Callable[[], Session] = _open_db,
        clock: Callable[[], datetime] = lambda: datetime.now(IST),
    ) -> None:
        self._manager_factory = manager_factory
        self._db_factory = db_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._notice: dict[str, str] = {}

    # --- public API ------------------------------------------------------------------

    def quote_rows(self, symbols: Sequence[str], user_id: str = LOCAL_USER) -> list[dict[str, Any]]:
        # Callers match rows back by the symbol they passed, so echo it unchanged.
        asked: dict[str, str] = {}
        for s in symbols:
            try:
                asked.setdefault(parse_symbol(s).display, s)
            except ValueError:
                continue
        refs = [parse_symbol(s) for s in asked.values()]
        if not refs:
            return []
        quotes = self._with_sessions(
            user_id, "quotes", lambda data, sessions: data.quotes(sessions, refs)
        )
        rows = []
        for q in quotes or []:
            row = _quote_row(q)
            row["symbol"] = asked.get(q.instrument.display, row["symbol"])
            rows.append(row)
        return rows

    def daily_bars(self, symbol: str, days: int, user_id: str = LOCAL_USER) -> list[DailyBar]:
        refs = self._refs([symbol])
        if not refs:
            return []
        end = self._clock()
        start = end - timedelta(days=max(5, int(max(1, days) * _CALENDAR_FACTOR)))
        candles = self._with_sessions(
            user_id,
            "daily candles",
            lambda data, sessions: data.candles(sessions, refs[0], Interval.DAY_1, start, end),
        )
        bars = [
            DailyBar(
                date=c.ts.astimezone(IST).date().isoformat(),
                open=float(c.open),
                close=float(c.close),
                high=float(c.high),
                low=float(c.low),
                volume=float(c.volume),
            )
            for c in candles or []
        ]
        return bars[-days:] if days > 0 else bars

    def data_notice(self, user_id: str = LOCAL_USER) -> str:
        """Why India data is missing for this user, or ``""`` if the last call worked."""
        with self._lock:
            return self._notice.get(user_id, "")

    # --- internals -------------------------------------------------------------------

    def _refs(self, symbols: Sequence[str]) -> list[InstrumentRef]:
        refs: list[InstrumentRef] = []
        for s in symbols:
            try:
                refs.append(parse_symbol(s))
            except ValueError:
                continue
        return refs

    def _with_sessions(self, user_id: str, what: str, call: Callable[[Any, list[Any]], Any]) -> Any:
        try:
            manager = self._manager_factory()
        except IndiaDataNotConfigured:
            self._set_notice(user_id, "Indian market data is not available in this process.")
            return None
        db = self._db_factory()
        try:
            sessions = manager.sessions(db, user_id)
            if not sessions:
                self._set_notice(
                    user_id,
                    "No broker is connected for Indian market data. Connect Kite, Upstox or "
                    "Angel One under Data sources.",
                )
                return None
            try:
                result = call(manager.data, sessions)
            except AllProvidersFailed as e:
                if e.needs_reconnect:
                    manager.mark_needs_reconnect(db, e.needs_reconnect, user_id)
                    names = ", ".join(e.needs_reconnect)
                    self._set_notice(
                        user_id,
                        f"Broker session expired ({names}). Log in again under Data sources.",
                    )
                else:
                    self._set_notice(user_id, f"Indian market data unavailable: {e}")
                logger.warning("India %s unavailable: %s", what, e)
                return None
            self._set_notice(user_id, "")
            return result
        finally:
            db.close()

    def _set_notice(self, user_id: str, text: str) -> None:
        with self._lock:
            self._notice[user_id] = text


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _quote_row(q: Quote) -> dict[str, Any]:
    """Same keys as the upstream quote row, plus data provenance and circuit limits."""
    return {
        "symbol": display_symbol(q.instrument),
        "name": q.instrument.tradingsymbol,
        "market": "IN",
        "current_price": float(q.last_price),
        "change_pct": _num(q.change_pct),
        "change_amount": _num(q.change),
        "prev_close": _num(q.prev_close),
        "open_price": _num(q.open),
        "high_price": _num(q.high),
        "low_price": _num(q.low),
        "volume": None if q.volume is None else float(q.volume),
        "turnover": None,
        "turnover_rate": None,
        "volume_ratio": None,
        "pe_ratio": None,
        "circulating_market_value": None,
        "total_market_value": None,
        "upper_circuit": _num(q.upper_circuit),
        "lower_circuit": _num(q.lower_circuit),
        "source": q.source,
        "quality": q.quality.value,
    }


_bridge: IndiaBridge | None = None
_bridge_lock = threading.Lock()


def get_india_bridge() -> IndiaBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = IndiaBridge()
        return _bridge
