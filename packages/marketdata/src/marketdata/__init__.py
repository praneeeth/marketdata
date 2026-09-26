"""marketdata: India market data (NSE/BSE) from the user's own broker, plus global cues.

- ``marketdata.india``: a read-only provider interface with Kite, Upstox and Angel One
  adapters (bring your own key), a dev-only yfinance adapter and the per-user
  ``IndiaMarketData`` service.
- ``marketdata.global_cues``: world indices, crude, gold and USD/INR for context.
"""

from marketdata.errors import MarketDataError

__version__ = "0.2.0"

__all__ = ["MarketDataError", "__version__"]
