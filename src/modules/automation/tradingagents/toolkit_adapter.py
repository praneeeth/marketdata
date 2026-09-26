"""Adapt PanWatch's India data into the TradingAgents data flow.

TradingAgents routes data requests through ``tradingagents.dataflows.interface.route_to_vendor``
to vendors such as yfinance and has no public injection point, so we monkeypatch
``route_to_vendor`` (and ``load_ohlcv``). Every ticker is an Indian (NSE/BSE) instrument:
requests are served from the current task's PanWatch snapshot, i.e. the user's own
broker data. Upstream vendors are reachable only in development
(``ALLOW_UNOFFICIAL_DATA`` + a non-production ``APP_ENV``); production returns an explicit
"data unavailable" note instead (plan item X8).

This depends on TradingAgents internals; a version bump must re-run the adapter tests.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


# Cache: inside the patch context the data PanWatch fetched lives here and patched calls
# return it. A ContextVar rather than a module dict: deep analyses run in asyncio.to_thread
# workers and to_thread copies the context, so concurrent tasks each get their own copy and
# two stocks analysed at once never mix data.
_PANWATCH_DATA: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_TA_PANWATCH_DATA", default={}
)

# The current request's trace_id; toolkit hit/miss logs are attributed to this analysis
_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_TA_TRACE_ID", default=""
)
_CANCEL_EVENT: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "_TA_CANCEL_EVENT", default=None
)

# Every upstream monkeypatch and PanWatch data injection lives here; other modules use only these entry points.
__all__ = [
    "TradingAgentsCancelled",
    "is_panwatch_routable",
    "panwatch_data_context",
    "patch_route_to_vendor",
]


class TradingAgentsCancelled(RuntimeError):
    """The TradingAgents task has finished; leftover workers must not make further requests."""


def _raise_if_cancelled() -> None:
    event = _CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise TradingAgentsCancelled("TradingAgents task cancelled")


def _cache() -> dict[str, Any]:
    """The current context's PanWatch data snapshot (isolated per concurrent task)."""
    return _PANWATCH_DATA.get()


@contextmanager
def panwatch_data_context(
    data: dict[str, Any],
    trace_id: str = "",
    cancel_event: threading.Event | None = None,
):
    """Wrap TradingAgents calls in this context manager to inject data.

    Args:
        data: dict with stock / quote / klines / events / capital_flow
        trace_id: this analysis' trace_id, so toolkit hit logs belong to the run

    The data is restored on exit. Built on a ContextVar, so concurrent tasks (and their
    to_thread workers) never interfere.
    """
    token = _PANWATCH_DATA.set(dict(data))
    tid_token = _CURRENT_TRACE_ID.set(trace_id or "")
    cancel_token = _CANCEL_EVENT.set(cancel_event)
    try:
        yield
    finally:
        _CANCEL_EVENT.reset(cancel_token)
        _PANWATCH_DATA.reset(token)
        _CURRENT_TRACE_ID.reset(tid_token)


def _emit_toolkit_log(level: str, action: str, method_name: str, symbol: str, **extra):
    """Log toolkit hit/miss/passthrough under the same trace_id so the UI can show it."""
    from src.platform.observability.log_context import log_context

    trace_id = _CURRENT_TRACE_ID.get()
    if not trace_id:
        # Without a trace_id, log normally (filterable by logger in the log centre)
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")
        return
    with log_context(
        trace_id=trace_id,
        agent_name="tradingagents",
        event="ta_toolkit",
        tags={"action": action, "method": method_name, "symbol": symbol, **extra},
    ):
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")


def is_panwatch_routable(symbol: str) -> bool:
    """Every ticker is an Indian (NSE/BSE) instrument served from the user's broker."""
    return bool(symbol and str(symbol).strip())


# Upstream tool modules use `from tradingagents.dataflows.interface import route_to_vendor`,
# an import-time binding: each module holds a reference to the original function.
# Patching the source module alone is not enough; every import site's module-level name
# must be replaced so all calls go through the interceptor.
_ROUTE_TO_VENDOR_IMPORT_SITES = (
    "tradingagents.agents.utils.fundamental_data_tools",
    "tradingagents.agents.utils.news_data_tools",
    "tradingagents.agents.utils.core_stock_tools",
    "tradingagents.agents.utils.technical_indicators_tools",
)


# Patch reference count: concurrent deep analyses share one installation. The first to
# enter saves the real route_to_vendor and installs the patch at every import site; the
# last to leave restores it. Data isolation comes from _PANWATCH_DATA (a ContextVar), so
# the patch is installed once per process, avoiding the old nested-restore race.
_patch_lock = threading.Lock()
_patch_refcount = 0
_patch_saved_sites: list[tuple[Any, str, Any]] = []  # (module, attr_name, original_value)
_real_route_to_vendor = None  # the real route_to_vendor (for upstream vendors)


_DATE_ARGUMENT = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _looks_like_date(value: Any) -> bool:
    """Whether a route_to_vendor string argument is a date rather than a ticker.

    Tickers can be numeric (BSE codes such as 500209), so a heuristic like
    ``value[:4].isdigit()`` is wrong; only values that clearly match a date format are skipped.
    """
    return isinstance(value, str) and bool(_DATE_ARGUMENT.fullmatch(value.strip()))


def _extract_requested_symbol(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Extract the ticker from upstream tool arguments (get_global_news starts with a date)."""
    for value in args:
        if isinstance(value, str) and value.strip() and not _looks_like_date(value):
            return value.strip()
    return str(kwargs.get("symbol") or kwargs.get("ticker") or "").strip()


def _cached_symbol() -> str:
    stock = _cache().get("stock")
    return str(getattr(stock, "symbol", "") or "").strip()


def _upstream_allowed() -> bool:
    """Upstream vendors (Yahoo etc.) only in development, like the yfinance adapter (X8)."""
    from marketdata.india.yfinance_dev import unofficial_data_allowed

    return unofficial_data_allowed(os.environ)


def _upstream_args(symbol: str, args: tuple[Any, ...]) -> list[Any]:
    """Map the ticker argument to Yahoo's .NS/.BO form for development fallthrough."""
    from marketdata.india.yfinance_dev import ticker_for
    from src.platform.marketdata.india_bridge import parse_symbol

    new_args = list(args)
    try:
        yahoo = ticker_for(parse_symbol(symbol))
    except Exception:  # noqa: BLE001 - leave the argument unchanged if it can't be mapped
        return new_args
    for i, a in enumerate(new_args):
        if isinstance(a, str) and a == symbol:
            new_args[i] = yahoo
            break
    return new_args


def _patched_route_to_vendor(method_name: str, *args, **kwargs):
    """Serve TradingAgents' data calls from the user's broker data (India-only).

    Same signature as upstream ``route_to_vendor(method, *args, **kwargs)``; tickers are
    passed positionally (``get_news(ticker, start, end)``, ``get_stock_data(symbol, ...)``,
    ``get_global_news(curr_date, ...)`` has none). Stateless: the symbol comes from the
    call and the data from ``_cache()`` (the current task's context), so concurrent tasks
    never mix.

    Order: PanWatch snapshot (broker data) -> in development only, upstream vendors with the
    ticker mapped to Yahoo's .NS/.BO -> otherwise an explicit "data unavailable" note.
    Production never falls through to Yahoo (plan item X8).
    """
    _raise_if_cancelled()
    symbol = _extract_requested_symbol(args, kwargs)
    cached_symbol = _cached_symbol()
    # Calls without a ticker (e.g. get_global_news) stay on the current stock instead of
    # pulling unrelated global headlines.
    if not symbol and cached_symbol:
        symbol = cached_symbol

    if symbol and _cache():
        if cached_symbol and symbol != cached_symbol:
            # Never pass off this task's snapshot as another stock's data.
            _emit_toolkit_log(
                "warning", "DEGRADE", method_name, symbol,
                reason=f"snapshot symbol mismatch: cached={cached_symbol}",
                extra_args=_args_summary(args),
            )
            return _data_unavailable_message(
                method_name,
                symbol,
                RuntimeError(f"PanWatch snapshot is for {cached_symbol}, not {symbol}"),
            )
        try:
            result = _serve_from_panwatch(method_name, symbol, kwargs, args=args)
            _emit_toolkit_log(
                "info", "HIT", method_name, symbol,
                chars=len(result), snippet=str(result)[:4000],
                source="panwatch", extra_args=_args_summary(args),
            )
            return result
        except NotImplementedError:
            _emit_toolkit_log(
                "info", "MISS", method_name, symbol,
                reason="PanWatch does not implement this method",
            )
        except Exception as e:  # noqa: BLE001 - one tool's failure must not sink the run
            _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
            return f"[PanWatch error: {e}]"

    if not _upstream_allowed():
        _emit_toolkit_log(
            "info", "DEGRADE", method_name, symbol or "(none)",
            reason="upstream vendors are disabled outside development",
            extra_args=_args_summary(args),
        )
        return _data_unavailable_message(
            method_name,
            symbol,
            RuntimeError("no broker data for this request; upstream vendors are disabled"),
        )

    # Development only: upstream vendors, with the ticker mapped to Yahoo's .NS/.BO form.
    # Upstream tools depend on external keys/services and can fail; a missing tool result
    # must not fail the whole run.
    try:
        # Only the task's own stock is a ticker; other first arguments (e.g. the macro
        # indicator "fed_funds_rate") must reach the vendor unchanged.
        mapped = _upstream_args(symbol, args) if symbol and symbol == cached_symbol else list(args)
        upstream_result = _real_route_to_vendor(method_name, *mapped, **kwargs)
    except Exception as e:
        if not _is_market_data_failure(e):
            raise
        logger.warning(f"[TA toolkit] upstream {method_name} unavailable: {e}")
        _emit_toolkit_log(
            "warning", "DEGRADE", method_name, symbol or "(none)",
            error=str(e)[:200], extra_args=_args_summary(args),
        )
        return _data_unavailable_message(method_name, symbol, e)
    upstream_str = str(upstream_result) if upstream_result is not None else ""
    _emit_toolkit_log(
        "info", "PASSTHROUGH", method_name, symbol or "(none)",
        chars=len(upstream_str), snippet=upstream_str[:4000],
        source="upstream (development only)", extra_args=_args_summary(args),
    )
    return upstream_result


@contextmanager
def patch_route_to_vendor():
    """Monkeypatch tradingagents.dataflows.interface.route_to_vendor and every import site.

    Requests are answered from _PANWATCH_DATA (the current context), i.e. the user's broker
    data; see _patched_route_to_vendor for the fallthrough policy.

    Reference count plus lock: concurrent deep analyses share one installation; the first
    to enter installs and the last to leave uninstalls, and _real_route_to_vendor always holds
    the real function.

    If tradingagents is not installed this context manager does nothing.
    """
    global _patch_refcount, _real_route_to_vendor

    try:
        from tradingagents.dataflows import interface as ta_interface
    except ImportError:
        logger.warning("[TA toolkit] tradingagents is not installed; skipping the monkeypatch")
        yield
        return

    if not hasattr(ta_interface, "route_to_vendor"):
        logger.warning(
            "[TA toolkit] route_to_vendor is missing (the upstream API may have changed); "
            "using the default vendor path"
        )
        yield
        return

    # Also take over load_ohlcv: upstream get_verified_market_snapshot bypasses route_to_vendor.
    _ensure_load_ohlcv_patched()
    _ensure_market_snapshot_patched()

    import importlib
    with _patch_lock:
        if _patch_refcount == 0:
            # First in: save the real function and install at the source and every import site
            # (`from ... import route_to_vendor` binds at import time, so patching only the
            # source leaves the tool modules' references unchanged)
            _real_route_to_vendor = ta_interface.route_to_vendor
            _patch_saved_sites.clear()
            ta_interface.route_to_vendor = _patched_route_to_vendor
            _patch_saved_sites.append((ta_interface, "route_to_vendor", _real_route_to_vendor))
            for module_path in _ROUTE_TO_VENDOR_IMPORT_SITES:
                try:
                    mod = importlib.import_module(module_path)
                except ImportError:
                    continue
                if hasattr(mod, "route_to_vendor"):
                    _patch_saved_sites.append((mod, "route_to_vendor", mod.route_to_vendor))
                    mod.route_to_vendor = _patched_route_to_vendor
                    logger.debug(f"[TA toolkit] patched route_to_vendor in {module_path}")
        _patch_refcount += 1

    try:
        yield
    finally:
        with _patch_lock:
            _patch_refcount -= 1
            if _patch_refcount <= 0:
                _patch_refcount = 0
                for mod, attr, orig in _patch_saved_sites:
                    setattr(mod, attr, orig)
                _patch_saved_sites.clear()


# ---------------------------------------------------------------------------
# load_ohlcv takeover
# Upstream get_verified_market_snapshot -> market_data_validator.load_ohlcv calls yfinance
# directly, bypassing route_to_vendor. Here every ticker's load_ohlcv reads PanWatch
# K-lines (the user's broker), so the patch is safe to install once per process.
# ---------------------------------------------------------------------------
_LOAD_OHLCV_PATCHED = False
_real_load_ohlcv: Any = None
_LOAD_OHLCV_IMPORT_SITES = (
    "tradingagents.dataflows.market_data_validator",
    "tradingagents.dataflows.interface",
    "tradingagents.dataflows.y_finance",
)

_MARKET_SNAPSHOT_PATCHED = False
_real_build_verified_market_snapshot: Any = None
_MARKET_SNAPSHOT_IMPORT_SITES = (
    "tradingagents.agents.utils.market_data_validation_tools",
)


def _market_for_symbol(symbol: str):
    """Map a TradingAgents ticker to a PanWatch market: India is the only one."""
    from src.platform.marketdata.models import MarketCode

    return MarketCode.IN


def _build_panwatch_ohlcv_df(symbol: str, curr_date: str):
    """Build a DataFrame shaped like native load_ohlcv (Date/Open/High/Low/Close/Volume) from PanWatch K-lines."""
    _raise_if_cancelled()
    import pandas as pd

    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    market = _market_for_symbol(symbol)
    # collect() already prepared the K-lines for this analysis; the verified snapshot needs the
    # same data and must not fetch again because of upstream's default lookback=750.
    cached_klines = _cache().get("klines")
    cached_stock = _cache().get("stock")
    cached_symbol = getattr(cached_stock, "symbol", "") if cached_stock is not None else ""
    cache_matches_symbol = bool(cached_symbol) and str(cached_symbol) == str(symbol)
    if cache_matches_symbol and isinstance(cached_klines, (list, tuple)):
        klines = list(cached_klines)
    else:
        klines = KlineCollector(market).get_klines(symbol, days=750)
    if not klines:
        return None
    df = pd.DataFrame(
        [
            {
                "Date": k.date,
                "Open": k.open,
                "High": k.high,
                "Low": k.low,
                "Close": k.close,
                "Volume": k.volume,
            }
            for k in klines
        ]
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    if curr_date:
        try:
            df = df[df["Date"] <= pd.to_datetime(curr_date)]
        except Exception:
            pass
    return df.reset_index(drop=True)


def _is_market_data_failure(error: Exception) -> bool:
    """Whether an exception means external market data is unavailable, not a program error."""
    name = type(error).__name__.lower()
    detail = str(error).lower()
    return (
        name in {
            "yfratelimiterror",
            "nomarketdataerror",
            "vendorratelimiterror",
            "vendornotconfigurederror",
        }
        or any(token in detail for token in (
            "api_key",
            "api key",
            "not configured",
            "too many requests",
            "rate limited",
            "no market data",
            "no price data",
            "no ohlcv data",
            "yahoo finance returned no rows",
            "no timezone found",
            "possibly delisted",
            "connection",
            "timeout",
            "service unavailable",
            "server error",
            "http error",
        ))
    )


def _data_unavailable_message(method_name: str, symbol: str, error: Exception) -> str:
    """An explicit degraded result for upstream agents; unavailable data must never read as neutral."""
    return (
        "DATA_UNAVAILABLE: "
        f"method={method_name}; symbol={symbol or 'N/A'}; reason={str(error)[:300]}. "
        "Do not infer missing values or treat this as neutral evidence. "
        "State the data limitation and request manual review when it affects the decision."
    )


def _load_panwatch_ohlcv_or_raise(symbol: str, curr_date: str, *, fallback: bool = False):
    """Read K-lines from the broker; raise the standard data error when there is no OHLCV."""
    df = None
    try:
        df = _build_panwatch_ohlcv_df(symbol, curr_date)
    except Exception as exc:
        logger.warning(f"[TA toolkit] load_ohlcv fetch failed symbol={symbol}: {exc}")
    if df is not None and not df.empty:
        action = "FALLBACK" if fallback else "HIT"
        _emit_toolkit_log(
            "info", action, "load_ohlcv", symbol, rows=int(len(df)), source="marketdata"
        )
        return df

    _emit_toolkit_log("warning", "MISS", "load_ohlcv", symbol, source="marketdata")
    try:
        from tradingagents.dataflows.errors import NoMarketDataError
        raise NoMarketDataError(
            symbol, symbol,
            "Could not fetch K-lines; check the broker connection or try again later",
        )
    except ImportError:
        raise RuntimeError(
            f"Could not fetch K-lines symbol={symbol} (check the broker connection)"
        )


def _panwatch_load_ohlcv(symbol: str, curr_date: str, *args, **kwargs):
    """Every ticker reads the user's broker K-lines (India-only)."""
    _raise_if_cancelled()
    if is_panwatch_routable(symbol):
        return _load_panwatch_ohlcv_or_raise(symbol, curr_date)

    try:
        upstream_df = _real_load_ohlcv(symbol, curr_date, *args, **kwargs)
        if upstream_df is not None and not upstream_df.empty:
            _emit_toolkit_log("info", "PASSTHROUGH", "load_ohlcv", symbol, source="yfinance")
            return upstream_df
        logger.warning(f"[TA toolkit] Yahoo OHLCV empty; falling back to broker data symbol={symbol}")
    except Exception as exc:
        if not _is_market_data_failure(exc):
            raise
        logger.warning(f"[TA toolkit] Yahoo OHLCV unavailable; falling back to broker data symbol={symbol}: {exc}")
        _emit_toolkit_log("warning", "DEGRADE", "load_ohlcv", symbol, source="yfinance", error=str(exc)[:200])
    _raise_if_cancelled()
    return _load_panwatch_ohlcv_or_raise(symbol, curr_date, fallback=True)


def _safe_build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Any = None,
) -> str:
    """When no market data is available, return a constraining note instead of letting one tool error stop the graph."""
    try:
        return _real_build_verified_market_snapshot(
            symbol, curr_date, look_back_days, indicators=indicators
        )
    except Exception as exc:
        if not _is_market_data_failure(exc):
            raise
        _emit_toolkit_log(
            "warning", "DEGRADE", "get_verified_market_snapshot", symbol,
            source="all market data sources", error=str(exc)[:200],
        )
        return (
            f"## Verified market data unavailable for {symbol.upper()}\n\n"
            f"- Requested analysis date: {curr_date}\n"
            "- No usable OHLCV data was returned by the configured providers.\n\n"
            "Do not make exact price, indicator, stop-loss, or trade-action claims. "
            "State that market data is temporarily unavailable and limit the analysis "
            "to non-price qualitative context."
        )


def _ensure_load_ohlcv_patched() -> None:
    """Install the load_ohlcv patch once per process (idempotent)."""
    global _LOAD_OHLCV_PATCHED, _real_load_ohlcv
    if _LOAD_OHLCV_PATCHED:
        return
    try:
        from tradingagents.dataflows import stockstats_utils
    except ImportError:
        return
    if not hasattr(stockstats_utils, "load_ohlcv"):
        return
    import importlib

    with _patch_lock:
        if _LOAD_OHLCV_PATCHED:
            return
        _real_load_ohlcv = stockstats_utils.load_ohlcv
        stockstats_utils.load_ohlcv = _panwatch_load_ohlcv
        for module_path in _LOAD_OHLCV_IMPORT_SITES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            if getattr(mod, "load_ohlcv", None) is not None:
                mod.load_ohlcv = _panwatch_load_ohlcv
                logger.debug(f"[TA toolkit] patched load_ohlcv in {module_path}")
        _LOAD_OHLCV_PATCHED = True
        logger.info("[TA toolkit] load_ohlcv taken over (reads the user's broker K-lines)")


def _ensure_market_snapshot_patched() -> None:
    """Make the verified snapshot return a safe note when all market data fails, instead of failing the graph."""
    global _MARKET_SNAPSHOT_PATCHED, _real_build_verified_market_snapshot
    if _MARKET_SNAPSHOT_PATCHED:
        return
    try:
        from tradingagents.dataflows import market_data_validator
    except ImportError:
        return
    if not hasattr(market_data_validator, "build_verified_market_snapshot"):
        return
    import importlib

    with _patch_lock:
        if _MARKET_SNAPSHOT_PATCHED:
            return
        _real_build_verified_market_snapshot = market_data_validator.build_verified_market_snapshot
        market_data_validator.build_verified_market_snapshot = _safe_build_verified_market_snapshot
        for module_path in _MARKET_SNAPSHOT_IMPORT_SITES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            if getattr(mod, "build_verified_market_snapshot", None) is not None:
                mod.build_verified_market_snapshot = _safe_build_verified_market_snapshot
                logger.debug(f"[TA toolkit] patched build_verified_market_snapshot in {module_path}")
        _MARKET_SNAPSHOT_PATCHED = True
        logger.info("[TA toolkit] Safe fallback installed for the verified market snapshot")


def _args_summary(args: tuple) -> str:
    """Short form of positional args for the log's extra_args, to tell repeated get_indicators calls apart.

    e.g. ("INFY", "macd", "2026-05-17", 30) -> "macd, 2026-05-17, 30" (symbol skipped)
    """
    if not args:
        return ""
    parts = []
    for i, a in enumerate(args):
        if i == 0 and isinstance(a, str) and len(a) == 6 and a.isdigit():
            continue  # skip the symbol (shown separately)
        s = str(a)
        if len(s) > 40:
            s = s[:40] + "..."
        parts.append(s)
    return ", ".join(parts)


def _stock_meta_header(symbol: str) -> str:
    """Stock metadata (company, market, price) prefixed to every tool result.

    The model must be told the company behind a ticker explicitly ("TATAMOTORS = Tata
    Motors"); otherwise it may guess a different company from the ticker.
    """
    stock = _cache().get("stock")
    quote = _cache().get("quote") or {}

    name = ""
    market = "IN"
    industry = ""
    if stock is not None:
        name = getattr(stock, "name", "") or ""
        market_obj = getattr(stock, "market", None)
        market = getattr(market_obj, "value", str(market_obj or "IN"))
    if not name and isinstance(quote, dict):
        name = quote.get("name") or ""
    if isinstance(quote, dict):
        industry = quote.get("industry") or ""

    market_label = {"IN": "India (NSE/BSE)"}.get(market, market)
    cur_price = _attr(quote, "current_price", "") or _attr(quote, "price", "")
    change_pct = _attr(quote, "change_pct", "")

    lines = [
        f"[Stock Metadata] symbol={symbol}, name={name or 'N/A'}, market={market_label}",
    ]
    if industry:
        lines.append(f"  Industry: {industry}")
    if cur_price:
        try:
            lines.append(
                f"  Current price: {float(cur_price):.2f}"
                + (f" ({float(change_pct):+.2f}%)" if change_pct != "" else "")
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        "  IMPORTANT: This is an Indian (NSE/BSE) ticker. DO NOT guess the company "
        "from the ticker code alone — use the name above."
    )
    return "\n".join(lines)


def _serve_from_panwatch(method_name: str, symbol: str, kwargs: dict, args: tuple = ()) -> str:
    """Build the format TradingAgents expects (CSV / JSON strings) from _cache() (this context's data).

    Upstream vendor methods return various types, usually a str (formatted CSV, table or JSON).
    Common method names are supported; **unknown methods raise NotImplementedError** and the
    router decides what happens next.

    Every branch starts with the stock metadata so the model never guesses the company.
    """
    method = (method_name or "").lower()
    header = _stock_meta_header(symbol)

    # 1a) Single indicator: get_indicators(symbol, indicator_name, curr_date, look_back_days)
    # Upstream calls it once per indicator (macd/rsi/kdj/boll/...); returning the same CSV eight
    # times wastes context, so each call gets that indicator's current value and a short note.
    if "indicator" in method:
        if args and len(args) >= 2:
            indicator = str(args[1]).lower()
            return f"{header}\n\n{_render_single_indicator(indicator, symbol)}"
        # No indicator argument: fall back to the K-line CSV
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No data available for indicators on {symbol}]"

    # 1b) Full K-line CSV: get_stockstats / get_yfin_data / get_stock_data
    if any(k in method for k in (
        "stockstats", "yfin", "ohlcv", "kline", "price", "stock_data",
    )):
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No kline data available from PanWatch for {symbol}]"

    # 2) Announcements/events/news: get_finnhub_news / get_news / get_events / get_global_news / get_insider_*
    if any(k in method for k in ("news", "event", "announce", "insider")):
        events = _cache().get("events") or []
        if events:
            return f"{header}\n\n{_events_to_text(events, limit=20)}"
        return (
            f"{header}\n\n[No company-specific news/events available for {symbol}. "
            "DO NOT pull unrelated global news as a substitute — focus the analysis "
            "on the metadata above and other tool outputs.]"
        )

    # 3) Fund flows (large-order net inflow). Don't match "cashflow"/"cash_flow": that is the cash flow statement
    if "capital" in method or ("flow" in method and "cash" not in method):
        flow = _cache().get("capital_flow")
        if flow:
            return f"{header}\n\n{_flow_to_text(flow)}"
        return f"{header}\n\n[No capital flow data available for {symbol}]"

    # 4) Fundamentals / statements: full figures when reported financials exist, otherwise quote-based
    financial = _cache().get("financial")
    if "fundamental" in method or "financial" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_fundamentals_summary
            return f"{header}\n\n{render_fundamentals_summary(financial)}"
        return f"{header}\n\n{_quote_to_lightweight_fundamentals(symbol)}"
    if "income" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_income_statement
            return f"{header}\n\n{render_income_statement(financial)}"
        return (
            f"{header}\n\n[Income statement not available for {symbol}. "
            "Avoid invented revenue/earnings numbers.]"
        )
    if "balance" in method or "sheet" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_balance_sheet
            return f"{header}\n\n{render_balance_sheet(financial)}"
        return (
            f"{header}\n\n[Balance sheet not available for {symbol}. "
            "Avoid invented assets/liabilities numbers.]"
        )
    if "cashflow" in method or "cash_flow" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_cashflow
            return f"{header}\n\n{render_cashflow(financial)}"
        return (
            f"{header}\n\n[Cash flow statement not available for {symbol}. "
            "Avoid invented cash flow numbers.]"
        )

    # Unknown: let the router decide
    raise NotImplementedError(f"no panwatch backing for {method_name}")


def _render_single_indicator(indicator: str, symbol: str) -> str:
    """Return one indicator's current value (macd/rsi/kdj/boll/...) instead of the full K-line CSV.

    Source: the dataclass KlineCollector.get_technical_indicators already computed.
    """
    tech = _cache().get("technical")
    if not tech:
        # Not precomputed: fall back to the K-line CSV (the model computes it)
        klines = _cache().get("klines") or []
        if klines:
            return (
                f"[Indicator query: {indicator}] (no precomputed value, "
                f"returning raw K-line CSV for self-calculation)\n\n"
                f"{_klines_to_csv(klines[-30:])}"  # 30 rows are enough
            )
        return f"[No data available for indicator '{indicator}' on {symbol}]"

    ind = indicator.lower()
    lines = [f"[Technical Indicator: {indicator.upper()}] for {symbol}"]
    handled = False

    def _g(name):
        return _attr(tech, name, None)

    if "macd" in ind:
        dif, dea, hist = _g("macd_dif"), _g("macd_dea"), _g("macd_hist")
        cross = _g("macd_cross") or ""
        lines.append(f"- DIF: {dif} | DEA: {dea} | Hist: {hist}")
        if cross:
            lines.append(f"- Latest cross: {cross}")
        handled = True
    if "rsi" in ind:
        lines.append(f"- RSI(6): {_g('rsi6')} | RSI(12): {_g('rsi12')} | RSI(24): {_g('rsi24')}")
        st = _g("rsi_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "kdj" in ind:
        lines.append(f"- K: {_g('kdj_k')} | D: {_g('kdj_d')} | J: {_g('kdj_j')}")
        st = _g("kdj_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "boll" in ind:
        lines.append(
            f"- Upper: {_g('boll_upper')} | Mid: {_g('boll_mid')} | Lower: {_g('boll_lower')}"
        )
        st = _g("boll_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if any(x in ind for x in ("ma", "sma", "ema")) and not handled:
        lines.append(
            f"- MA5: {_g('ma5')} | MA10: {_g('ma10')} | MA20: {_g('ma20')} | MA60: {_g('ma60')}"
        )
        trend = _g("trend")
        if trend:
            lines.append(f"- Trend: {trend}")
        handled = True
    if any(x in ind for x in ("vol", "volume")):
        lines.append(f"- Volume ratio: {_g('volume_ratio')} | Trend: {_g('volume_trend')}")
        handled = True

    if not handled:
        # Unknown indicator: dump the full indicator summary
        lines.append("(indicator name not specifically recognized — returning full snapshot)")
        for attr in (
            "ma5", "ma10", "ma20", "ma60",
            "macd_dif", "macd_dea", "macd_hist", "macd_cross",
            "rsi6", "rsi12", "rsi24", "rsi_status",
            "kdj_k", "kdj_d", "kdj_j", "kdj_status",
            "boll_upper", "boll_mid", "boll_lower", "boll_status",
            "volume_ratio", "volume_trend", "trend",
        ):
            v = _g(attr)
            if v is not None and v != "":
                lines.append(f"- {attr}: {v}")
    return "\n".join(lines)


def _quote_to_lightweight_fundamentals(symbol: str) -> str:
    """Light fundamentals from the quote (market cap, P/E, turnover) so the model has real data."""
    quote = _cache().get("quote") or {}
    if not isinstance(quote, dict):
        return f"[No lightweight fundamentals available for {symbol}]"

    lines = ["[Lightweight Fundamentals (from PanWatch real-time quote)]"]
    fields = [
        ("PE ratio", "pe_ratio"),
        ("Total market cap", "total_market_value"),
        ("Circulating market cap", "circulating_market_value"),
        ("Turnover rate (%)", "turnover_rate"),
        ("Current price", "current_price"),
        ("Today change (%)", "change_pct"),
        ("Today open", "open_price"),
        ("Today high", "high_price"),
        ("Today low", "low_price"),
        ("Prev close", "prev_close"),
        ("Volume (shares)", "volume"),
        ("Turnover (INR)", "turnover"),
    ]
    has_any = False
    for label, key in fields:
        v = quote.get(key)
        if v is not None and v != "":
            lines.append(f"- {label}: {v}")
            has_any = True
    if not has_any:
        return f"[No lightweight fundamentals available for {symbol}]"
    lines.append("")
    lines.append(
        "Note: This is real-time market data, NOT a substitute for full financial "
        "statements. Use it as a sanity check (e.g. valuation level via P/E, liquidity "
        "via turnover) rather than as the basis for revenue/earnings claims."
    )
    return "\n".join(lines)


def _klines_to_csv(klines) -> str:
    """KlineData list -> CSV string.

    Upstream TradingAgents expects: date,open,high,low,close,volume
    """
    if not klines:
        return "date,open,high,low,close,volume\n"
    lines = ["date,open,high,low,close,volume"]
    for k in klines:
        date_v = getattr(k, "date", None) or (k.get("date") if isinstance(k, dict) else "")
        open_v = _attr(k, "open")
        high_v = _attr(k, "high")
        low_v = _attr(k, "low")
        close_v = _attr(k, "close")
        vol_v = _attr(k, "volume")
        lines.append(f"{date_v},{open_v},{high_v},{low_v},{close_v},{vol_v}")
    return "\n".join(lines)


def _events_to_text(events, limit: int = 20) -> str:
    if not events:
        return "No recent announcements or events"
    out = []
    for ev in events[:limit]:
        title = getattr(ev, "title", None) or (
            ev.get("title") if isinstance(ev, dict) else str(ev)
        )
        ts = getattr(ev, "publish_time", None) or (
            ev.get("publish_time") if isinstance(ev, dict) else ""
        )
        out.append(f"- [{ts}] {title}")
    return "\n".join(out)


def _flow_to_text(flow) -> str:
    if isinstance(flow, list):
        flow = flow[0] if flow else None
    if not flow:
        return "No fund-flow data"
    main_net = _attr(flow, "main_net_inflow")
    main_pct = _attr(flow, "main_net_inflow_pct")
    return f"Large-order net inflow: {main_net} / {main_pct}%"


def _attr(obj, name, default=""):
    if hasattr(obj, name):
        v = getattr(obj, name)
        return v if v is not None else default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default
