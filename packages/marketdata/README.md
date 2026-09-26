# marketdata

India market data (NSE/BSE) for the Candlewise India fork, fetched with each user's own
broker credentials, plus a small set of global market cues for context.

## Layout

- `marketdata.india`: typed data (`Decimal` prices, timezone-aware timestamps) and a
  read-only `MarketDataProvider` protocol with no order methods.
  - Adapters: `kite` (Zerodha Kite Connect v3), `upstox` and `angel` (Angel One
    SmartAPI) over `httpx`, with no broker SDKs. `yfinance_dev` is for development only.
  - `service.IndiaMarketData`: per-user failover and credential-keyed caching.
    `instrument_cache.InstrumentCache` holds a per-credential instrument master with
    single-flight loading.
  - `session.ProviderSession` and `Secret`: credentials never appear in `repr`, logs,
    exceptions or pickles.
- `marketdata.global_cues`: world indices, Brent, gold and USD/INR. The interim source
  is free, delayed Yahoo data, labelled unofficial.

Design notes: `docs/adr.md` (ADR-007) and `docs/india-fork/PLAN.md` (Phase 2).

## Tests

```bash
python -m pytest -q          # from packages/marketdata
```

The adapter contract suite (`tests/india/test_contract.py`) runs every provider against
fixtures written by hand from each provider's public docs. They are not recordings of
live traffic; see `tests/india/fixtures/README.md`.
