"""Build option chains for brokers that have no option-chain endpoint (Kite, Angel One).

The chain is assembled from the instrument master (contracts for one underlying and
expiry) plus a batch quote of those contracts. Nothing here ranks or selects strikes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal

from marketdata.india.types import (
    DataQuality,
    Instrument,
    InstrumentRef,
    InstrumentType,
    OptionChain,
    OptionChainRow,
    OptionQuote,
    Quote,
)

# Index names as quoted on the cash market vs. the ``name`` their derivatives carry in
# broker instrument masters. (verify) against each broker's master when adding indices.
INDEX_DERIVATIVE_NAMES: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}


def derivative_name(underlying: InstrumentRef) -> str:
    return INDEX_DERIVATIVE_NAMES.get(underlying.tradingsymbol.upper(), underlying.tradingsymbol)


def select_contracts(
    instruments: Iterable[Instrument], underlying: InstrumentRef, expiry: date
) -> list[Instrument]:
    name = derivative_name(underlying)
    return [
        i
        for i in instruments
        if i.instrument_type in (InstrumentType.CALL, InstrumentType.PUT)
        and i.expiry == expiry
        and i.strike is not None
        and (i.underlying or i.name) == name
    ]


def build_chain(
    *,
    underlying: InstrumentRef,
    expiry: date,
    contracts: Sequence[Instrument],
    fetch_quotes: Callable[[Sequence[InstrumentRef]], list[Quote]],
    spot: Decimal | None,
    source: str,
    quality: DataQuality,
) -> OptionChain:
    quotes = {q.instrument.tradingsymbol: q for q in fetch_quotes([c.ref for c in contracts])}
    calls: dict[Decimal, OptionQuote] = {}
    puts: dict[Decimal, OptionQuote] = {}
    for c in contracts:
        if c.strike is None:
            continue
        q = quotes.get(c.tradingsymbol)
        oq = OptionQuote(
            instrument=c.ref,
            last_price=q.last_price if q else None,
            oi=q.oi if q else None,
            volume=q.volume if q else None,
        )
        (calls if c.instrument_type is InstrumentType.CALL else puts)[c.strike] = oq
    strikes = sorted(set(calls) | set(puts))
    return OptionChain(
        underlying=underlying,
        expiry=expiry,
        source=source,
        quality=quality,
        spot=spot,
        rows=tuple(OptionChainRow(s, calls.get(s), puts.get(s)) for s in strikes),
    )


def batched(items: Sequence[InstrumentRef], size: int) -> Iterable[Sequence[InstrumentRef]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
