"""India market data: a read-only provider interface with bring-your-own-key broker adapters.

See ``docs/india-fork/PLAN.md`` (Phase 2) and ``docs/adr.md`` (ADR-007).
"""

from marketdata.india.errors import (
    BadResponse,
    InstrumentNotResolved,
    InvalidCredentials,
    NotSupported,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SessionExpired,
    UnofficialDataDisabled,
)
from marketdata.india.provider import MarketDataProvider
from marketdata.india.session import ProviderSession, Secret
from marketdata.india.types import (
    IST,
    Candle,
    Capability,
    CorporateAction,
    CorporateActionKind,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentRef,
    InstrumentType,
    Interval,
    OptionChain,
    OptionChainRow,
    OptionQuote,
    Quote,
)

__all__ = [
    "IST",
    "BadResponse",
    "Candle",
    "Capability",
    "CorporateAction",
    "CorporateActionKind",
    "DataQuality",
    "Exchange",
    "Instrument",
    "InstrumentNotResolved",
    "InstrumentRef",
    "InstrumentType",
    "Interval",
    "InvalidCredentials",
    "MarketDataProvider",
    "NotSupported",
    "OptionChain",
    "OptionChainRow",
    "OptionQuote",
    "ProviderError",
    "ProviderSession",
    "ProviderUnavailable",
    "Quote",
    "RateLimited",
    "Secret",
    "SessionExpired",
    "UnofficialDataDisabled",
]
