"""Daily K-lines and technical indicators (India-only).

K-lines come from the current user's broker connections through the India bridge, which
caches per credential. The indicator maths below (MA, MACD, RSI, KDJ, Bollinger, ATR,
patterns) is market-neutral and unchanged from upstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.platform.marketdata.collectors.market_http import fetch_source
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)

# Log label for who triggered a fetch ("price_alert", "outcome_eval", ...).
kline_source = fetch_source


def clear_kline_cache() -> None:
    """Kept for callers and tests. K-lines are cached per credential in the India layer."""


def get_index_klines(index_code: str, market: MarketCode = MarketCode.IN, days: int = 120) -> list[KlineData]:
    """Daily K-lines of an index such as "NIFTY 50" or "BSE:SENSEX"."""
    return KlineCollector(MarketCode.IN).get_klines(index_code, days=days)


@dataclass
class KlineData:
    """One daily K-line."""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


@dataclass
class TechnicalIndicators:
    """Technical indicators."""

    # Moving averages
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    # MACD
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    macd_cross: str | None = None  # golden cross / death cross
    macd_cross_days: int | None = None  # days since the last cross
    # RSI
    rsi6: float | None = None
    rsi12: float | None = None
    rsi24: float | None = None
    # KDJ
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    kdj_cross: str | None = None  # golden cross / death cross
    # Bollinger bands
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    boll_width: float | None = None  # band width, %
    # Volume
    volume_ratio: float | None = None  # volume ratio (today's volume / 5-day average)
    volume_ma5: float | None = None
    volume_ma10: float | None = None
    volume_trend: str | None = None  # volume up / volume down / volume flat
    # Change
    change_5d: float | None = None
    change_20d: float | None = None
    # Range
    amplitude: float | None = None  # today's range
    amplitude_avg5: float | None = None  # 5-day average range
    # Volatility (ATR)
    atr: float | None = None  # average true range (absolute)
    atr_pct: float | None = None  # ATR / last close * 100 (relative volatility %)
    # Support and resistance (several horizons)
    support_s: float | None = None  # short-term support (5 days)
    support_m: float | None = None  # medium-term support (20 days)
    support_l: float | None = None  # long-term support (60 days)
    resistance_s: float | None = None  # short-term resistance
    resistance_m: float | None = None  # medium-term resistance
    resistance_l: float | None = None  # long-term resistance
    # Legacy fields
    support: float | None = None
    resistance: float | None = None
    # Candlestick pattern
    kline_pattern: str | None = None  # doji / hammer / engulfing, etc.


def _calculate_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(data: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if not data:
        return []
    result = [data[0]]
    multiplier = 2 / (period + 1)
    for price in data[1:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


def _calculate_atr(klines: list[KlineData], period: int = 14) -> float | None:
    """ATR (average true range).

    TR = max(high-low, |high-prevClose|, |low-prevClose|).
    Like the other indicators here, it is the simple mean of the last ``period`` TRs
    (not Wilder smoothing), so it is easy to reproduce by hand.

    Needs at least period+1 candles (for period TRs with a previous close); returns None
    on insufficient or bad data instead of raising.
    """
    try:
        if not klines or len(klines) < period + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(klines)):
            cur = klines[i]
            prev_close = klines[i - 1].close
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev_close),
                abs(cur.low - prev_close),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None


def _calculate_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]] | None:
    """MACD; returns the full series so crosses can be detected."""
    if len(closes) < slow + signal:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, macd_hist


def _calculate_rsi(closes: list[float], period: int) -> float | None:
    """Relative strength index."""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # Use the last ``period`` days
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_kdj(
    klines: list[KlineData], n: int = 9, m1: int = 3, m2: int = 3
) -> tuple[list[float], list[float], list[float]] | None:
    """KDJ; returns the full series."""
    if len(klines) < n:
        return None

    k_values = []
    d_values = []
    j_values = []

    for i in range(n - 1, len(klines)):
        period_klines = klines[i - n + 1 : i + 1]
        highest = max(k.high for k in period_klines)
        lowest = min(k.low for k in period_klines)
        close = klines[i].close

        if highest == lowest:
            rsv = 50
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        if not k_values:
            k = 50
            d = 50
        else:
            k = (2 / 3) * k_values[-1] + (1 / 3) * rsv
            d = (2 / 3) * d_values[-1] + (1 / 3) * k

        j = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    return k_values, d_values, j_values


def _calculate_boll(
    closes: list[float], period: int = 20, num_std: int = 2
) -> tuple[float, float, float, float] | None:
    """Bollinger bands: upper, middle, lower and width."""
    if len(closes) < period:
        return None

    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = variance**0.5

    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid * 100 if mid > 0 else 0

    return upper, mid, lower, width


def _detect_kline_pattern(klines: list[KlineData]) -> str | None:
    """Detect the latest candlestick pattern."""
    if len(klines) < 2:
        return None

    curr = klines[-1]
    prev = klines[-2]

    body = abs(curr.close - curr.open)
    upper_shadow = curr.high - max(curr.close, curr.open)
    lower_shadow = min(curr.close, curr.open) - curr.low
    total_range = curr.high - curr.low

    if total_range == 0:
        return None

    body_ratio = body / total_range

    # Doji: very small body
    if body_ratio < 0.1:
        if upper_shadow > body * 2 and lower_shadow > body * 2:
            return "doji"
        elif upper_shadow > body * 3:
            return "gravestone doji"
        elif lower_shadow > body * 3:
            return "dragonfly doji"

    # Hammer: long lower shadow, body near the top
    if lower_shadow > body * 2 and upper_shadow < body * 0.5:
        if curr.close > curr.open:
            return "hammer (bullish body)"
        else:
            return "hammer (bearish body)"

    # Inverted hammer: long upper shadow
    if upper_shadow > body * 2 and lower_shadow < body * 0.5:
        if curr.close > curr.open:
            return "inverted hammer"
        else:
            return "shooting star"

    # Engulfing
    prev_body = abs(prev.close - prev.open)
    if body > prev_body * 1.5:
        if prev.close < prev.open and curr.close > curr.open:  # bearish then bullish
            if curr.close > prev.open and curr.open < prev.close:
                return "bullish engulfing"
        elif prev.close > prev.open and curr.close < curr.open:  # bullish then bearish
            if curr.open > prev.close and curr.close < prev.open:
                return "bearish engulfing"

    # Long bullish / bearish candle
    if body_ratio > 0.7:
        change_pct = (curr.close - curr.open) / curr.open * 100 if curr.open > 0 else 0
        if change_pct > 3:
            return "long bullish candle"
        elif change_pct < -3:
            return "long bearish candle"

    return None


def _find_cross_days(
    series1: list[float], series2: list[float], cross_type: str
) -> int | None:
    """Days since the most recent cross."""
    if len(series1) < 2 or len(series2) < 2:
        return None

    for i in range(len(series1) - 2, -1, -1):
        if cross_type == "golden cross":
            # Golden cross: series1 crosses series2 from below
            if series1[i] <= series2[i] and series1[i + 1] > series2[i + 1]:
                return len(series1) - 1 - i
        else:
            # Death cross: series1 crosses series2 from above
            if series1[i] >= series2[i] and series1[i + 1] < series2[i + 1]:
                return len(series1) - 1 - i

    return None


class KlineCollector:
    """Daily K-lines from the user's broker (India-only)."""

    def __init__(self, market: MarketCode = MarketCode.IN):
        self.market = market

    def get_klines(self, symbol: str, days: int = 60) -> list[KlineData]:
        """Daily K-lines, oldest first. Empty (with a data notice) when no broker can answer."""
        from src.platform.marketdata.india_bridge import get_india_bridge

        bars = get_india_bridge().daily_bars(symbol, max(1, int(days or 1)))
        return [KlineData(date=b.date, open=b.open, close=b.close, high=b.high,
                          low=b.low, volume=b.volume) for b in bars]

    def get_technical_indicators(
        self, symbol: str = "", klines: list[KlineData] | None = None
    ) -> TechnicalIndicators:
        """Technical indicators (pass already-fetched klines to avoid another fetch)."""
        if klines is None:
            klines = self.get_klines(symbol, days=120)

        if not klines:
            return TechnicalIndicators()

        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]

        # Moving averages
        ma5 = _calculate_ma(closes, 5)
        ma10 = _calculate_ma(closes, 10)
        ma20 = _calculate_ma(closes, 20)
        ma60 = _calculate_ma(closes, 60)

        # MACD
        macd_result = _calculate_macd(closes)
        macd_dif, macd_dea, macd_hist = None, None, None
        macd_cross, macd_cross_days = None, None
        if macd_result:
            dif_list, dea_list, hist_list = macd_result
            macd_dif = dif_list[-1]
            macd_dea = dea_list[-1]
            macd_hist = hist_list[-1]
            # Golden cross / death cross
            if macd_dif > macd_dea:
                macd_cross = "golden cross"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "golden cross")
            else:
                macd_cross = "death cross"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "death cross")

        # RSI
        rsi6 = _calculate_rsi(closes, 6)
        rsi12 = _calculate_rsi(closes, 12)
        rsi24 = _calculate_rsi(closes, 24)

        # KDJ
        kdj_k, kdj_d, kdj_j = None, None, None
        kdj_cross = None
        kdj_result = _calculate_kdj(klines)
        if kdj_result:
            k_list, d_list, j_list = kdj_result
            kdj_k = k_list[-1]
            kdj_d = d_list[-1]
            kdj_j = j_list[-1]
            if kdj_k > kdj_d:
                kdj_cross = "golden cross"
            else:
                kdj_cross = "death cross"

        # Bollinger bands
        boll_upper, boll_mid, boll_lower, boll_width = None, None, None, None
        boll_result = _calculate_boll(closes)
        if boll_result:
            boll_upper, boll_mid, boll_lower, boll_width = boll_result

        # Volume
        volume_ma5 = _calculate_ma(volumes, 5) if volumes else None
        volume_ma10 = _calculate_ma(volumes, 10) if volumes else None
        volume_ratio = None
        volume_trend = None
        if volumes and volume_ma5 and volume_ma5 > 0:
            volume_ratio = volumes[-1] / volume_ma5
            if volume_ratio > 1.5:
                volume_trend = "volume up"
            elif volume_ratio < 0.7:
                volume_trend = "volume down"
            else:
                volume_trend = "volume flat"

        # Change
        change_5d = None
        change_20d = None
        if len(closes) >= 6:
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        if len(closes) >= 21:
            change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100

        # Range
        amplitude = None
        amplitude_avg5 = None
        if klines:
            curr = klines[-1]
            if curr.low > 0:
                amplitude = (curr.high - curr.low) / curr.low * 100
            if len(klines) >= 5:
                amps = []
                for k in klines[-5:]:
                    if k.low > 0:
                        amps.append((k.high - k.low) / k.low * 100)
                if amps:
                    amplitude_avg5 = sum(amps) / len(amps)

        # ATR (volatility): the stock's own baseline, used by the adaptive unusual-move check
        atr = _calculate_atr(klines, period=14)
        atr_pct = None
        if atr is not None and closes and closes[-1]:
            atr_pct = round(atr / closes[-1] * 100, 2)

        # Support and resistance levels
        support_s, support_m, support_l = None, None, None
        resistance_s, resistance_m, resistance_l = None, None, None
        if len(klines) >= 5:
            support_s = min(k.low for k in klines[-5:])
            resistance_s = max(k.high for k in klines[-5:])
        if len(klines) >= 20:
            support_m = min(k.low for k in klines[-20:])
            resistance_m = max(k.high for k in klines[-20:])
        if len(klines) >= 60:
            support_l = min(k.low for k in klines[-60:])
            resistance_l = max(k.high for k in klines[-60:])

        # Legacy fields
        support = support_m
        resistance = resistance_m

        # Candlestick pattern
        kline_pattern = _detect_kline_pattern(klines)

        return TechnicalIndicators(
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            macd_dif=macd_dif,
            macd_dea=macd_dea,
            macd_hist=macd_hist,
            macd_cross=macd_cross,
            macd_cross_days=macd_cross_days,
            rsi6=rsi6,
            rsi12=rsi12,
            rsi24=rsi24,
            kdj_k=kdj_k,
            kdj_d=kdj_d,
            kdj_j=kdj_j,
            kdj_cross=kdj_cross,
            boll_upper=boll_upper,
            boll_mid=boll_mid,
            boll_lower=boll_lower,
            boll_width=boll_width,
            volume_ratio=volume_ratio,
            volume_ma5=volume_ma5,
            volume_ma10=volume_ma10,
            volume_trend=volume_trend,
            change_5d=change_5d,
            change_20d=change_20d,
            amplitude=amplitude,
            amplitude_avg5=amplitude_avg5,
            atr=atr,
            atr_pct=atr_pct,
            support_s=support_s,
            support_m=support_m,
            support_l=support_l,
            resistance_s=resistance_s,
            resistance_m=resistance_m,
            resistance_l=resistance_l,
            support=support,
            resistance=resistance,
            kline_pattern=kline_pattern,
        )

    def get_kline_summary(self, symbol: str) -> dict:
        """K-line summary for prompts and the UI."""
        klines = self.get_klines(symbol, days=120)
        if not klines:
            return {"error": "No K-line data"}
        indicators = self.get_technical_indicators(klines=klines)

        # Last 5 days
        recent_5 = klines[-5:] if len(klines) >= 5 else klines
        up_days = sum(
            1
            for i, k in enumerate(recent_5)
            if i > 0 and k.close > recent_5[i - 1].close
        )

        # Trend
        trend = "insufficient data"
        if indicators.ma5 and indicators.ma10 and indicators.ma20:
            if indicators.ma5 > indicators.ma10 > indicators.ma20:
                trend = "bullish alignment"
            elif indicators.ma5 < indicators.ma10 < indicators.ma20:
                trend = "bearish alignment"
            else:
                trend = "mixed MAs"

        # MACD status
        macd_status = "no data"
        if indicators.macd_cross:
            days_str = (
                f" ({indicators.macd_cross_days}d)"
                if indicators.macd_cross_days
                else ""
            )
            macd_status = f"{indicators.macd_cross}{days_str}"

        # RSI status
        rsi_status = None
        if indicators.rsi6 is not None:
            if indicators.rsi6 > 80:
                rsi_status = "overbought"
            elif indicators.rsi6 > 70:
                rsi_status = "strong"
            elif indicators.rsi6 < 20:
                rsi_status = "oversold"
            elif indicators.rsi6 < 30:
                rsi_status = "weak"
            else:
                rsi_status = "neutral"

        # KDJ status
        kdj_status = None
        if indicators.kdj_k is not None and indicators.kdj_d is not None:
            if indicators.kdj_j is not None and indicators.kdj_j > 100:
                kdj_status = f"{indicators.kdj_cross}/overbought"
            elif indicators.kdj_j is not None and indicators.kdj_j < 0:
                kdj_status = f"{indicators.kdj_cross}/oversold"
            else:
                kdj_status = indicators.kdj_cross

        # Bollinger status
        boll_status = None
        last_close = klines[-1].close if klines else None
        if last_close and indicators.boll_upper and indicators.boll_lower:
            if last_close > indicators.boll_upper:
                boll_status = "above upper band"
            elif last_close < indicators.boll_lower:
                boll_status = "below lower band"
            elif indicators.boll_width:
                if indicators.boll_width < 5:
                    boll_status = "bands narrowing"
                elif indicators.boll_width > 15:
                    boll_status = "bands widening"
                else:
                    boll_status = "normal range"

        last_date = klines[-1].date if klines else None
        now = datetime.now(timezone.utc).isoformat()

        return {
            # meta
            "timeframe": "1d",
            "computed_at": now,
            "asof": last_date,
            "params": {
                "ma": [5, 10, 20, 60],
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"periods": [6, 12, 24]},
                "kdj": {"n": 9, "m1": 3, "m2": 3},
                "boll": {"period": 20, "num_std": 2},
                "support_resistance": {"windows": [5, 20, 60]},
            },
            "last_close": last_close,
            "recent_5_up": up_days,
            "trend": trend,
            # MACD
            "macd_status": macd_status,
            "macd_cross": indicators.macd_cross,
            "macd_cross_days": indicators.macd_cross_days,
            "macd_hist": indicators.macd_hist,
            # RSI
            "rsi6": indicators.rsi6,
            "rsi_status": rsi_status,
            # KDJ
            "kdj_k": indicators.kdj_k,
            "kdj_d": indicators.kdj_d,
            "kdj_j": indicators.kdj_j,
            "kdj_status": kdj_status,
            # Bollinger bands
            "boll_upper": indicators.boll_upper,
            "boll_mid": indicators.boll_mid,
            "boll_lower": indicators.boll_lower,
            "boll_width": indicators.boll_width,
            "boll_status": boll_status,
            # Volume
            "volume_ratio": indicators.volume_ratio,
            "volume_trend": indicators.volume_trend,
            # Moving averages
            "ma5": indicators.ma5,
            "ma10": indicators.ma10,
            "ma20": indicators.ma20,
            "ma60": indicators.ma60,
            # Change
            "change_5d": indicators.change_5d,
            "change_20d": indicators.change_20d,
            # Range
            "amplitude": indicators.amplitude,
            "amplitude_avg5": indicators.amplitude_avg5,
            # Volatility (ATR)
            "atr": indicators.atr,
            "atr_pct": indicators.atr_pct,
            # Support and resistance
            "support_s": indicators.support_s,
            "support_m": indicators.support_m,
            "support_l": indicators.support_l,
            "resistance_s": indicators.resistance_s,
            "resistance_m": indicators.resistance_m,
            "resistance_l": indicators.resistance_l,
            # Legacy fields
            "support": indicators.support,
            "resistance": indicators.resistance,
            # Candlestick pattern
            "kline_pattern": indicators.kline_pattern,
        }
