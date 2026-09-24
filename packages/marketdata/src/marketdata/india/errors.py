"""Errors raised by India market data providers.

Messages are built only from provider names, status codes and the provider's own error
text. They never include request headers, bodies or credentials.
"""

from __future__ import annotations

from marketdata.errors import MarketDataError


class ProviderError(MarketDataError):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class SessionExpired(ProviderError):
    """The broker session is missing, invalid or expired. The user must reconnect."""


class InvalidCredentials(ProviderError):
    """Login was rejected (wrong API key/secret, request token or PIN/TOTP)."""


class RateLimited(ProviderError):
    def __init__(self, provider: str, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(provider, message)
        self.retry_after_s = retry_after_s


class ProviderUnavailable(ProviderError):
    """Network failure or a 5xx after retries."""


class BadResponse(ProviderError):
    """The provider answered with a shape we don't understand, or rejected the request."""


class NotSupported(ProviderError):
    """This provider does not offer the requested capability."""


class InstrumentNotResolved(ProviderError):
    """The instrument ref has no ID for this provider; resolve it via the instrument master."""


class UnofficialDataDisabled(MarketDataError):
    """Unofficial data sources are off (production, or ALLOW_UNOFFICIAL_DATA is not set)."""
