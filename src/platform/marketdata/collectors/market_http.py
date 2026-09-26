"""Shared HTTP helper for quote/data collection.

Gathers boilerplate that used to be scattered across collectors, so each file doesn't write its own with its own gaps:
- **System proxy**: trust_env=True by default, following the process env HTTP_PROXY/NO_PROXY (set by apply_proxy_env from the UI's http_proxy). Direct without a proxy.
- **Per-host throttling**: a minimum interval between requests to one domain, smoothing sequential/concurrent bursts (third parties rate-limit bursts).
- **Backoff retries**: backoff + jitter on empty responses/errors.
- **Call source tag**: one contextvar shared project-wide, so failure logs carry [src=xxx] naming the task that triggered them.

The source tag is shared globally: once a scheduled entry point wraps itself in `with fetch_source("xxx"):`,
failure logs from every collector in that task (K-line/quote/flows/...) carry the same source.
asyncio.to_thread propagates contextvars, so a tag set in async scheduling reaches the worker thread too.
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Call source tag (shared globally) ───────────────────────────────────
_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fetch_source", default=""
)


@contextmanager
def fetch_source(name: str):
    """Label the caller of a data fetch (e.g. "price_alert") for failure logs."""
    token = _FETCH_SOURCE.set(name or "")
    try:
        yield
    finally:
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# ── Per-host, per-process throttling ─────────────────────────────────────
_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """Keep requests to one host at least min_interval_s apart, smoothing sequential/concurrent bursts."""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


# ── Shared synchronous GET ───────────────────────────────────────────────
def market_get(
    url: str,
    *,
    host_key: str,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "text",  # "text" | "json" | "content"
    encoding: str | None = None,  # forced decoding (e.g. "gbk")
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,  # follow the process env proxy (HTTP_PROXY/NO_PROXY), set centrally by apply_proxy_env
    follow_redirects: bool = True,
    verify: bool = True,
) -> Any | None:
    """System proxy (env) + per-host throttling + backoff retries. Returns the parsed result on success; None and a log with the source on failure."""
    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
            ) as client:
                resp = client.get(url, params=params)
                if raise_for_status:
                    resp.raise_for_status()
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                if encoding:
                    return resp.content.decode(encoding, errors="ignore")
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        label = log_label or host_key
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} fetch failed{sym}: {last_err}{source_suffix()}")
    return None


# ── Lightweight TTL cache ─────────────────────────────────────────────────
# Equivalent to src/core/providers/cache.py, but defined in the lowest collection-layer module so collectors
# can reuse it directly, without collectors importing the providers package and creating an import cycle.
class TTLCache:
    """In-process memory TTL cache, thread-safe; expired keys are dropped lazily on the next get."""

    def __init__(self, default_ttl_sec: float = 20.0, max_size: int = 2048):
        self._default_ttl = default_ttl_sec
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if expires_at <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_sec: float | None = None) -> None:
        ttl = ttl_sec if ttl_sec is not None else self._default_ttl
        if ttl <= 0:
            return  # explicitly not cached
        expires = time.monotonic() + ttl
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest = min(self._store.items(), key=lambda kv: kv[1][1])
                del self._store[oldest[0]]
            self._store[key] = (value, expires)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
