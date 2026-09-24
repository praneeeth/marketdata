"""The read-only provider interface every India data adapter implements.

There are deliberately no order, position or funds methods. Broker integrations are
read-only by construction, and ``tests/compliance`` fails on order-placement code.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from marketdata.india.session import ProviderSession
from marketdata.india.types import (
    Candle,
    Capability,
    CorporateAction,
    DataQuality,
    Exchange,
    Instrument,
    InstrumentRef,
    Interval,
    OptionChain,
    Quote,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]
    quality: DataQuality

    def quotes(self, session: ProviderSession, refs: Sequence[InstrumentRef]) -> list[Quote]: ...

    def candles(
        self,
        session: ProviderSession,
        ref: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
    ) -> list[Candle]: ...

    def instruments(self, session: ProviderSession, exchange: Exchange) -> Iterator[Instrument]: ...

    def corporate_actions(
        self, session: ProviderSession, ref: InstrumentRef, start: date, end: date
    ) -> list[CorporateAction]: ...

    def option_chain(
        self, session: ProviderSession, underlying: InstrumentRef, expiry: date
    ) -> OptionChain: ...
