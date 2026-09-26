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


# 缓存:在 patch 上下文里把 PanWatch 拉好的数据塞这里,patch 命中时直接返回。
# 用 ContextVar 而非模块级 dict:深度分析跑在 asyncio.to_thread worker 线程,
# to_thread 会 copy_context(),每个并发任务拿到独立副本 —— 避免两只标的并发
# 分析时互相覆盖数据(广汽 601238 的报告混入赛力斯 601127 的 K线/新闻)。
_PANWATCH_DATA: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_TA_PANWATCH_DATA", default={}
)

# 跟随当前请求的 trace_id;toolkit hit/miss 日志归属到这次分析
_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_TA_TRACE_ID", default=""
)
_CANCEL_EVENT: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "_TA_CANCEL_EVENT", default=None
)

# 所有上游 monkeypatch 和 PanWatch 数据注入都集中在本文件；其它模块只依赖这些入口。
__all__ = [
    "TradingAgentsCancelled",
    "is_panwatch_routable",
    "panwatch_data_context",
    "patch_route_to_vendor",
]


class TradingAgentsCancelled(RuntimeError):
    """TradingAgents 任务已进入终态，禁止残留 worker 再发起外部请求。"""


def _raise_if_cancelled() -> None:
    event = _CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise TradingAgentsCancelled("TradingAgents task cancelled")


def _cache() -> dict[str, Any]:
    """读当前 context 的 PanWatch 数据快照(并发隔离)。"""
    return _PANWATCH_DATA.get()


@contextmanager
def panwatch_data_context(
    data: dict[str, Any],
    trace_id: str = "",
    cancel_event: threading.Event | None = None,
):
    """在调用 TradingAgents 的代码块周围用本 context manager 注入数据。

    Args:
        data: 含 stock / quote / klines / events / capital_flow 的字典
        trace_id: 本次分析的 trace_id,用于把 toolkit 命中日志归属到该运行

    退出 context 时还原数据。基于 ContextVar,并发任务(及其 to_thread worker)
    互不干扰。
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
    """把 toolkit hit/miss/passthrough 写进同 trace_id 的日志,前端可在弹窗看到。"""
    from src.platform.observability.log_context import log_context

    trace_id = _CURRENT_TRACE_ID.get()
    if not trace_id:
        # 没 trace_id 也打普通日志(可在日志中心按 logger 过滤)
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


# 上游 tool 文件用 `from tradingagents.dataflows.interface import route_to_vendor`,
# 这是 import-time binding,每个模块持有 **原函数引用**。
# 只 patch 源头模块属性不够 —— 必须把每个 import site 的 module-level
# 名字都替换掉,所有调用才会走我们的拦截。
_ROUTE_TO_VENDOR_IMPORT_SITES = (
    "tradingagents.agents.utils.fundamental_data_tools",
    "tradingagents.agents.utils.news_data_tools",
    "tradingagents.agents.utils.core_stock_tools",
    "tradingagents.agents.utils.technical_indicators_tools",
)


# patch 引用计数:多个并发深度分析共享同一次安装,第一个进入者保存真
# route_to_vendor 并装到所有 import site,最后一个退出才恢复。数据隔离靠
# _PANWATCH_DATA(ContextVar),patch 本身只需进程级安装一次 —— 消除原先
# "A 退出时把全局恢复成 B 的 _patched"的嵌套竞态。
_patch_lock = threading.Lock()
_patch_refcount = 0
_patch_saved_sites: list[tuple[Any, str, Any]] = []  # (module, attr_name, original_value)
_real_route_to_vendor = None  # 真 route_to_vendor(走上游 vendor 时用)


_DATE_ARGUMENT = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _looks_like_date(value: Any) -> bool:
    """判断 route_to_vendor 的字符串参数是不是日期，而不是用数字前缀误判 ticker。

    A/HK 股票代码本身就是纯数字（如 300624、00700），因此不能再用
    ``value[:4].isdigit()`` 之类的启发式过滤；只有明确匹配日期格式才跳过。
    """
    return isinstance(value, str) and bool(_DATE_ARGUMENT.fullmatch(value.strip()))


def _extract_requested_symbol(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """从上游工具参数提取 ticker，兼容 get_global_news 的日期首参。"""
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
    """Monkeypatch tradingagents.dataflows.interface.route_to_vendor + 所有 import sites。

    当请求 A 股代码时,从 _PANWATCH_DATA(当前 context)返回 PanWatch 已拉的数据。
    非 A 股放行到原函数。

    引用计数 + 锁:并发的多个深度分析共享同一次安装,第一个进入者装、最后一个
    退出才卸载,_real_route_to_vendor 永远保存真函数 —— 消除嵌套 patch 链错乱。

    如果 tradingagents 库未安装,本 context manager 是 no-op,不抛异常。
    """
    global _patch_refcount, _real_route_to_vendor

    try:
        from tradingagents.dataflows import interface as ta_interface
    except ImportError:
        logger.warning("[TA toolkit] tradingagents 未安装,跳过 monkeypatch")
        yield
        return

    if not hasattr(ta_interface, "route_to_vendor"):
        logger.warning(
            "[TA toolkit] route_to_vendor 不存在 (上游 API 可能变更),"
            "走默认 vendor 路径"
        )
        yield
        return

    # 同时接管 load_ohlcv:新上游 get_verified_market_snapshot 绕过 route_to_vendor。
    _ensure_load_ohlcv_patched()
    _ensure_market_snapshot_patched()

    import importlib
    with _patch_lock:
        if _patch_refcount == 0:
            # 第一个进入者:保存真函数并装到源头 + 所有 import sites
            # (`from ... import route_to_vendor` 是 import-time binding,只 patch
            # 源头不够 — 工具模块持有的原引用不变)
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
# load_ohlcv 接管
# 新上游 get_verified_market_snapshot → market_data_validator.load_ohlcv 直连 yfinance,
# 不经 route_to_vendor。A股(无 .SS)/港股(无 .HK)yfinance 拉不到 → NoMarketDataError,
# 整个 TradingAgents 分析失败。这里把 A股/港股的 load_ohlcv 改走 PanWatch K线;
# 非 PanWatch 标的(美股)透传原生 yfinance,故进程级永久安装安全、无需卸载。
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
    """用 PanWatch K线构建与原生 load_ohlcv 同结构的 DataFrame(Date/Open/High/Low/Close/Volume)。"""
    _raise_if_cancelled()
    import pandas as pd

    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    market = _market_for_symbol(symbol)
    # collect() 已经为本次分析准备了 K 线；验证快照只需要同一份数据，
    # 不应因为上游默认 lookback=750 再向东财发起一轮可能阻塞的请求。
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
    """判断异常是否表示外部行情不可用，而非程序自身错误。"""
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
    """给上游 agent 的显式降级结果，禁止将不可用数据默认为中性数据。"""
    return (
        "DATA_UNAVAILABLE: "
        f"method={method_name}; symbol={symbol or 'N/A'}; reason={str(error)[:300]}. "
        "Do not infer missing values or treat this as neutral evidence. "
        "State the data limitation and request manual review when it affects the decision."
    )


def _load_panwatch_ohlcv_or_raise(symbol: str, curr_date: str, *, fallback: bool = False):
    """读取 MarketData 的 K 线；没有可验证的 OHLCV 时抛出统一的数据错误。"""
    df = None
    try:
        df = _build_panwatch_ohlcv_df(symbol, curr_date)
    except Exception as exc:
        logger.warning(f"[TA toolkit] load_ohlcv MarketData 取数异常 symbol={symbol}: {exc}")
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
            "MarketData K线获取失败，请检查数据源、代理分流或稍后重试",
        )
    except ImportError:
        raise RuntimeError(
            f"MarketData K线获取失败 symbol={symbol}(请检查数据源或代理分流)"
        )


def _panwatch_load_ohlcv(symbol: str, curr_date: str, *args, **kwargs):
    """A/HK 直接走 MarketData；美股优先 Yahoo，失败时再降级 MarketData。"""
    _raise_if_cancelled()
    if is_panwatch_routable(symbol):
        return _load_panwatch_ohlcv_or_raise(symbol, curr_date)

    try:
        upstream_df = _real_load_ohlcv(symbol, curr_date, *args, **kwargs)
        if upstream_df is not None and not upstream_df.empty:
            _emit_toolkit_log("info", "PASSTHROUGH", "load_ohlcv", symbol, source="yfinance")
            return upstream_df
        logger.warning(f"[TA toolkit] Yahoo OHLCV 为空，降级 MarketData symbol={symbol}")
    except Exception as exc:
        if not _is_market_data_failure(exc):
            raise
        logger.warning(f"[TA toolkit] Yahoo OHLCV 不可用，降级 MarketData symbol={symbol}: {exc}")
        _emit_toolkit_log("warning", "DEGRADE", "load_ohlcv", symbol, source="yfinance", error=str(exc)[:200])
    _raise_if_cancelled()
    return _load_panwatch_ohlcv_or_raise(symbol, curr_date, fallback=True)


def _safe_build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Any = None,
) -> str:
    """行情全部不可用时返回约束性提示，避免单个工具异常中断图执行。"""
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
    """进程级幂等安装 load_ohlcv 补丁（A/HK 走 MarketData，美股可降级）。"""
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
        logger.info("[TA toolkit] load_ohlcv 已接管(A/HK走MarketData，美股Yahoo失败时降级)")


def _ensure_market_snapshot_patched() -> None:
    """将验证快照改为行情全失败时返回安全提示，而不是让图执行失败。"""
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
        logger.info("[TA toolkit] 已为验证行情快照安装安全降级")


def _args_summary(args: tuple) -> str:
    """把 positional args 简短打印,放进日志 extra_args 便于区分 get_indicators 多次调用。

    e.g. ("601238", "macd", "2026-05-17", 30) → "macd, 2026-05-17, 30"(跳过 symbol)
    """
    if not args:
        return ""
    parts = []
    for i, a in enumerate(args):
        if i == 0 and isinstance(a, str) and len(a) == 6 and a.isdigit():
            continue  # 跳过 symbol(已单独显示)
        s = str(a)
        if len(s) > 40:
            s = s[:40] + "..."
        parts.append(s)
    return ", ".join(parts)


def _stock_meta_header(symbol: str) -> str:
    """渲染标的元信息(公司名/市场/价格),作为所有工具返回的前缀。

    A 股 ticker 不在 yfinance/finnhub 数据集,LLM 不能从 ticker 反查公司名,
    必须显式告诉它"601127 = 赛力斯",否则会瞎编(如把 601127 当中国平安)。
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
    """从 _cache()(当前 context 的数据)构造 TradingAgents 期望的数据格式(CSV / JSON 字符串)。

    上游各 vendor 方法返回类型不一,通常是 str(已格式化的 CSV/表格/JSON)。
    本函数尽量兼容常见 method_name。**未识别的 method 返回空串,触发上游默认 vendor。**

    所有分支都以「标的元信息」开头,避免 LLM 在 A 股 ticker 上瞎编公司名。
    """
    method = (method_name or "").lower()
    header = _stock_meta_header(symbol)

    # 1a) 单指标查询:get_indicators(symbol, indicator_name, curr_date, look_back_days)
    # 上游对每个技术指标(macd/rsi/kdj/boll/...)各调一次,8 次返回相同 K线 CSV 是浪费。
    # 我们按 indicator 名返回简短的"该指标当前值 + 简要解读",避免重复污染上下文。
    if "indicator" in method:
        if args and len(args) >= 2:
            indicator = str(args[1]).lower()
            return f"{header}\n\n{_render_single_indicator(indicator, symbol)}"
        # 没传 indicator 参数:降级到 K 线 CSV
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No data available for indicators on {symbol}]"

    # 1b) K 线 / 股价完整 CSV:get_stockstats / get_yfin_data / get_stock_data
    if any(k in method for k in (
        "stockstats", "yfin", "ohlcv", "kline", "price", "stock_data",
    )):
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No kline data available from PanWatch for {symbol}]"

    # 2) 公告/事件/新闻:get_finnhub_news / get_news / get_events / get_global_news / get_insider_*
    if any(k in method for k in ("news", "event", "announce", "insider")):
        events = _cache().get("events") or []
        if events:
            return f"{header}\n\n{_events_to_text(events, limit=20)}"
        return (
            f"{header}\n\n[No company-specific news/events available for {symbol}. "
            "DO NOT pull unrelated global news as a substitute — focus the analysis "
            "on the metadata above and other tool outputs.]"
        )

    # 3) 资金流(主力资金净流入)— 注意:不要匹配 "cashflow" / "cash_flow",那是现金流量表
    if "capital" in method or ("flow" in method and "cash" not in method):
        flow = _cache().get("capital_flow")
        if flow:
            return f"{header}\n\n{_flow_to_text(flow)}"
        return f"{header}\n\n[No capital flow data available for {symbol}]"

    # 4) 基本面 / 财报:有真实 akshare 财务数据时返回完整指标,否则 fallback 到 quote
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

    # 未识别:让上游走默认 vendor
    raise NotImplementedError(f"no panwatch backing for {method_name}")


def _render_single_indicator(indicator: str, symbol: str) -> str:
    """按 indicator 名(macd/rsi/kdj/boll/...)返回该指标当前值,而不是全 K 线 CSV。

    数据源:KlineCollector.get_technical_indicators 已经算好的 dataclass。
    """
    tech = _cache().get("technical")
    if not tech:
        # 没预计算时,fallback 到 K 线 CSV(让 LLM 自己算)
        klines = _cache().get("klines") or []
        if klines:
            return (
                f"[Indicator query: {indicator}] (no precomputed value, "
                f"returning raw K-line CSV for self-calculation)\n\n"
                f"{_klines_to_csv(klines[-30:])}"  # 仅 30 条够
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
        # 未识别的指标:倾倒全部技术指标摘要
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
    """从 quote 拉"轻量基本面"(市值/PE/换手率/成交额),给 LLM 一些真实数据。"""
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
        ("Turnover (CNY)", "turnover"),
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
    """KlineData list → CSV 字符串。

    TradingAgents 上游期望:date,open,high,low,close,volume
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
        return "无近期公告/事件"
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
        return "无资金流向数据"
    main_net = _attr(flow, "main_net_inflow")
    main_pct = _attr(flow, "main_net_inflow_pct")
    return f"主力净流入:{main_net} / {main_pct}%"


def _attr(obj, name, default=""):
    if hasattr(obj, name):
        v = getattr(obj, name)
        return v if v is not None else default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default
