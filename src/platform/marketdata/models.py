from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class MarketCode(str, Enum):
    """India-only fork: NSE/BSE is the only market (owner decision 2026-09-25)."""

    IN = "IN"


@dataclass
class TradingSession:
    """One trading session."""
    start: time
    end: time


@dataclass
class MarketDef:
    """A market definition."""
    code: MarketCode
    name: str
    timezone: str
    sessions: list[TradingSession]
    symbol_pattern: str  # regex that valid symbols match

    def get_tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def is_trading_time(self, dt: datetime | None = None) -> bool:
        """Whether ``dt`` (default: now) falls inside a trading session."""
        if dt is None:
            dt = datetime.now(self.get_tz())
        else:
            dt = dt.astimezone(self.get_tz())

        # Closed on non-trading days. Imported lazily to avoid an import cycle.
        from src.platform.scheduling.trading_calendar import is_trading_day

        if not is_trading_day(self.code, dt.date()):
            return False

        current_time = dt.time()
        return any(
            session.start <= current_time <= session.end
            for session in self.sessions
        )


MARKETS: dict[MarketCode, MarketDef] = {
    # Normal session only; pre-open, special sessions and the NSE holiday calendar
    # arrive in Phase 3. Until then only weekends are treated as closed.
    MarketCode.IN: MarketDef(
        code=MarketCode.IN,
        name="India (NSE/BSE)",
        timezone="Asia/Kolkata",
        sessions=[
            TradingSession(time(9, 15), time(15, 30)),
        ],
        # "INFY", "NSE:INFY", "BSE:INFY", "M&M", "BAJAJ-AUTO", "NIFTY 50"
        symbol_pattern=r"^((NSE|BSE):)?[A-Z0-9][A-Z0-9&\-. ]{0,39}$",
    ),
}


@dataclass
class StockData:
    """Normalised quote data."""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float       # change %
    change_amount: float    # change amount
    volume: float           # volume (shares)
    turnover: float         # turnover (INR)
    open_price: float
    high_price: float
    low_price: float
    prev_close: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class IndexData:
    """Index data."""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float
    change_amount: float
    volume: float
    turnover: float
    timestamp: datetime = field(default_factory=datetime.now)
