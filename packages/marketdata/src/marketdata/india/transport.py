"""HTTP transport for broker adapters: per-credential throttling, retries, error mapping.

Unlike ``marketdata.http.market_get`` (which returns ``None`` on any failure), this raises
typed errors so callers can tell "session expired, reconnect" from "rate limited" from
"provider down". Error messages never contain headers, bodies or URLs with credentials.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from marketdata.india.errors import (
    BadResponse,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SessionExpired,
)
from marketdata.india.session import ProviderSession

ErrorMapper = Callable[[httpx.Response], ProviderError | None]

_MAX_ERROR_TEXT = 200


class Throttle:
    """Keeps at least ``min_interval_s`` between calls that share a key.

    Keys include the credential ID, so one user's burst never slows another user down and
    each broker app's own rate limit is respected.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, key: str, min_interval_s: float) -> None:
        if min_interval_s <= 0:
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next_allowed.get(key, now))
            self._next_allowed[key] = start + min_interval_s
        if start > now:
            self._sleep(start - now)


def provider_message(text: str) -> str:
    """Trim provider error text to a safe length for logs and exceptions."""
    text = " ".join(text.split())
    return text if len(text) <= _MAX_ERROR_TEXT else text[: _MAX_ERROR_TEXT - 1] + "…"


class Transport:
    def __init__(
        self,
        provider: str,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        throttle: Throttle | None = None,
        error_mapper: ErrorMapper | None = None,
        retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 15.0,
    ) -> None:
        self.provider = provider
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        if client is not None:
            self._client.base_url = httpx.URL(base_url)
        self._throttle = throttle or Throttle(sleep=sleep)
        self._error_mapper = error_mapper
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        session: ProviderSession | None,
        bucket: str = "default",
        min_interval_s: float = 0.0,
        retry: bool = True,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        key = f"{self.provider}:{session.credential_id if session else '-'}:{bucket}"
        attempts = self._retries + 1 if retry else 1
        for attempt in range(attempts):
            self._throttle.wait(key, min_interval_s)
            try:
                resp = self._client.request(
                    method, url, params=params, json=json, data=data, headers=headers
                )
            except httpx.HTTPError as e:
                # ``from None``: httpx exceptions carry the request, which carries headers.
                if attempt + 1 < attempts:
                    self._sleep(self._backoff_s * (attempt + 1))
                    continue
                raise ProviderUnavailable(
                    self.provider, f"network error ({type(e).__name__})"
                ) from None
            if resp.status_code >= 500 and attempt + 1 < attempts:
                self._sleep(self._backoff_s * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise self._map_error(resp)
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    def json(self, method: str, url: str, **kwargs: Any) -> Any:
        resp = self.request(method, url, **kwargs)
        try:
            return resp.json()
        except ValueError:
            raise BadResponse(self.provider, "response was not JSON") from None

    def _map_error(self, resp: httpx.Response) -> ProviderError:
        if resp.status_code == 429:
            return RateLimited(self.provider, "rate limited (HTTP 429)", _retry_after(resp.headers))
        if self._error_mapper is not None:
            mapped = self._error_mapper(resp)
            if mapped is not None:
                return mapped
        if resp.status_code in (401, 403):
            return SessionExpired(
                self.provider, f"session expired or invalid (HTTP {resp.status_code})"
            )
        if resp.status_code >= 500:
            return ProviderUnavailable(self.provider, f"HTTP {resp.status_code}")
        return BadResponse(self.provider, f"HTTP {resp.status_code}")

    def close(self) -> None:
        self._client.close()


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
