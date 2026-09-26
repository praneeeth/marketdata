from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List

from sqlalchemy.orm import Session

from src.platform.compliance import Feature, ensure_guarded
from src.platform.compliance.http import feature_gate
from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.automation.suggestion_pool import get_latest_suggestions
from src.modules.assistant.legacy_chat_tools import (
    build_stock_context,
    fetch_realtime_context,
    fetch_technical_context,
)
from src.platform.ai.ai_failover import get_configured_failover_client
from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Announcement analysis cache (announcements don't change; long TTL)
_ANN_CACHE = TTLCache(default_ttl_sec=21600)  # 6h

router = APIRouter()


class InsightItem(BaseModel):
    symbol: str = Field(..., description="Stock symbol")
    market: str = Field(..., description="Market: IN (NSE/BSE)")


class InsightsBatchRequest(BaseModel):
    items: List[InsightItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"Unsupported market: {market}")


@router.post("/batch")
def insights_batch(payload: InsightsBatchRequest):
    """Quotes + K-line summary + latest item, in one response."""
    if not payload.items:
        return []

    # 1) Quotes in bulk (by market)
    market_items: dict[MarketCode, list[str]] = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        market_items.setdefault(market_code, []).append(it.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        try:
            items = md_quote_rows(symbols, market_code.value)
        except Exception:
            items = []
        quotes_by_market[market_code] = {item["symbol"]: item for item in items}

    # 2) K-line summary (per stock, with a simple 60s cache)
    kline_by_symbol: dict[str, dict] = {}
    now = time.time()
    TTL = 60.0
    # module-level cache
    global _KLINE_CACHE
    try:
        _KLINE_CACHE
    except NameError:
        _KLINE_CACHE = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        cache_key = f"{market_code.value}:{it.symbol}"
        cached = _KLINE_CACHE.get(cache_key)
        summary = None
        if cached and (now - cached[0] < TTL):
            summary = cached[1]
        else:
            try:
                collector = KlineCollector(market_code)
                summary = collector.get_kline_summary(it.symbol)
            except Exception:
                summary = {}
            _KLINE_CACHE[cache_key] = (now, summary)
        kline_by_symbol[cache_key] = summary

    # 3) Latest item (suggestion pool)
    stock_keys = [(it.symbol, _parse_market(it.market).value) for it in payload.items]
    latest_sugs = get_latest_suggestions(stock_keys=stock_keys, include_expired=False)

    # 4) Combine
    results = []
    for it in payload.items:
        market_code = _parse_market(it.market)
        quote = quotes_by_market.get(market_code, {}).get(it.symbol)
        results.append({
            "symbol": it.symbol,
            "market": market_code.value,
            "quote": {
                "name": quote.get("name") if quote else None,
                "current_price": quote.get("current_price") if quote else None,
                "change_pct": quote.get("change_pct") if quote else None,
                "open_price": quote.get("open_price") if quote else None,
                "high_price": quote.get("high_price") if quote else None,
                "low_price": quote.get("low_price") if quote else None,
                "volume": quote.get("volume") if quote else None,
                "turnover": quote.get("turnover") if quote else None,
            },
            "kline_summary": kline_by_symbol.get(f"{market_code.value}:{it.symbol}", {}),
            "suggestion": latest_sugs.get(f"{market_code.value}:{it.symbol}"),
        })

    return results


class AddPositionEvalRequest(BaseModel):
    symbol: str
    market: str = "IN"
    current_quantity: float = Field(0, ge=0, description="Current quantity held (0 = new position)")
    current_cost: float = Field(0, ge=0, description="Current cost (per share)")
    add_quantity: float = Field(..., gt=0, description="Quantity to add")
    add_price: float = Field(..., gt=0, description="Price to add at")
    model_id: int | None = None


# Checked longest first: "not suitable" contains "suitable".
_VERDICTS = (("not suitable", "Not suitable"), ("cautious", "Cautious"), ("suitable", "Suitable"))


def _parse_verdict(text: str) -> str:
    """Roughly parse the verdict label from the AI reply; "Unknown" when none matches."""
    head = (text or "")[:120].lower()
    for needle, label in _VERDICTS:
        if needle in head:
            return label
    return "Unknown"


async def _fetch_fundamental_context(symbol: str, market: str) -> str:
    """Fundamentals summary: PE / turnover rate / market cap / today's range (from the live quote; empty on failure)."""
    try:
        mc = MarketCode.IN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        parts: list[str] = []
        if q.get("pe_ratio") not in (None, 0):
            parts.append(f"P/E {q['pe_ratio']}")
        if q.get("turnover_rate") not in (None, 0):
            parts.append(f"turnover rate {q['turnover_rate']}%")
        if q.get("circulating_market_value"):
            parts.append(f"free-float market cap {q['circulating_market_value']} Cr")
        if q.get("total_market_value"):
            parts.append(f"total market cap {q['total_market_value']} Cr")
        hi, lo, pc = q.get("high_price"), q.get("low_price"), q.get("prev_close")
        if hi and lo and pc:
            parts.append(f"today's range {(hi - lo) / pc * 100:.2f}%")
        return ("Fundamentals: " + ", ".join(parts)) if parts else ""
    except Exception as e:
        logger.debug(f"Fundamentals fetch failed {symbol}: {e}")
        return ""


async def _fetch_message_context(db: Session, symbol: str, market: str) -> str:
    """News summary: news/announcement titles from the last 3 days + recent local AI items/analyses (empty on failure)."""
    parts: list[str] = []
    try:
        from src.platform.marketdata.collectors.news_collector import NewsCollector

        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        name = stock.name if stock else symbol
        collector = NewsCollector.from_database()
        items = await collector.fetch_all(
            symbols=[symbol], since_hours=72, symbol_names={symbol: name}
        )
        items = sorted(items, key=lambda x: x.publish_time, reverse=True)[:5]
        if items:
            lines = [
                f"- {it.title} ({it.publish_time.strftime('%m-%d')})" for it in items
            ]
            parts.append("Recent news/announcements:\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"News context fetch failed {symbol}: {e}")

    try:
        ctx = build_stock_context(db, symbol, market)
        if ctx:
            parts.append(ctx)
    except Exception:
        pass

    return "\n\n".join(parts)


@router.post(
    "/add-position-eval",
    dependencies=[Depends(feature_gate(Feature.POSITION_CALCULATOR))],
)
async def add_position_eval(req: AddPositionEvalRequest, db: Session = Depends(get_db)):
    """Quick check before adding: server-side averaged cost + an AI verdict of Suitable / Cautious / Not suitable."""
    market = _parse_market(req.market).value
    cur_q = max(0.0, float(req.current_quantity or 0))
    cur_c = max(0.0, float(req.current_cost or 0))
    add_q = float(req.add_quantity)
    add_p = float(req.add_price)
    if add_q <= 0 or add_p <= 0:
        raise HTTPException(400, "Quantity and price to add must be greater than 0")

    new_q = cur_q + add_q
    new_cost = (cur_q * cur_c + add_q * add_p) / new_q if new_q > 0 else add_p
    is_add = cur_q > 0 and cur_c > 0
    dilute_abs = (cur_c - new_cost) if is_add else 0.0
    dilute_pct = (dilute_abs / cur_c * 100) if is_add and cur_c > 0 else 0.0
    action = "Add" if is_add else "Open position"

    # Context: live quote + fundamentals + technicals + news (news/announcements/local views)
    realtime = await fetch_realtime_context(req.symbol, market)
    fundamental = await _fetch_fundamental_context(req.symbol, market)
    technical = await fetch_technical_context(req.symbol, market)
    message = await _fetch_message_context(db, req.symbol, market)

    holding_line = (
        f"Currently holding {cur_q:.0f} shares at a cost of {cur_c:.3f} per share"
        if is_add
        else "No current position (this opens one)"
    )
    dilute_line = f", {dilute_abs:.3f} ({dilute_pct:.2f}%) below the current cost" if is_add else ""
    user_content = (
        f"Stock {market}:{req.symbol}\n"
        f"{holding_line}\n"
        f"Planned {action}: {add_q:.0f} shares @ {add_p:.3f}\n"
        f"Cost per share after this: {new_cost:.3f}{dilute_line}\n"
        + (f"{realtime}\n" if realtime else "")
        + (f"{fundamental}\n" if fundamental else "")
        + (f"{technical}\n" if technical else "")
        + (f"{message}\n" if message else "")
        + f"Considering valuation/fundamentals and news, assess whether this ({action}) is suitable."
    )
    system_prompt = (
        "You are a careful, practical stock research assistant. Using the holding, price, fundamentals, technicals and news the user gives,"
        f" assess whether this ({action}) is suitable. Don't invent data or promise returns.\n"
        "Reply strictly in this format, briefly:\n"
        "Verdict: Suitable / Cautious / Not suitable (pick one)\n"
        "Reasons:\n- (2-3 points covering the averaged cost, valuation/fundamentals, technicals and news)\n"
        "Risk: (the biggest risk in one sentence)"
    )

    try:
        client = get_configured_failover_client(db, req.model_id)
        content = await client.chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI assessment failed: {e}")

    return {
        "symbol": req.symbol,
        "market": market,
        "action": action,
        "new_cost": round(new_cost, 4),
        "dilute_abs": round(dilute_abs, 4),
        "dilute_pct": round(dilute_pct, 4),
        "total_quantity": new_q,
        "total_invested": round(new_q * new_cost, 2),
        "verdict": _parse_verdict(content),
        "content": content,
    }


# ── Announcement/results tone analysis (Phase B) ───────────────────────────
_ANN_TONES = {"positive": "Positive", "negative": "Negative", "neutral": "Neutral"}


def _parse_tone(text: str) -> str:
    """The tone word that appears first in the AI's field; "Neutral" when none does."""
    head = (text or "")[:60].lower()
    found = [(head.find(word), label) for word, label in _ANN_TONES.items() if word in head]
    return min(found)[1] if found else "Neutral"


async def _fetch_recent_announcements(symbol: str, name: str, limit: int = 5) -> list[dict]:
    """Announcements/news from the last 7 days; [] on failure."""
    try:
        from src.platform.marketdata.collectors.news_collector import ANNOUNCEMENT_SOURCE, NewsCollector

        items = await NewsCollector.from_database().fetch_all(
            symbols=[symbol], since_hours=168, symbol_names={symbol: name}
        )
        anns = [it for it in items if it.source == ANNOUNCEMENT_SOURCE] or items
        anns = sorted(anns, key=lambda x: x.publish_time, reverse=True)[:limit]
        return [
            {
                "title": a.title,
                "time": a.publish_time.strftime("%Y-%m-%d %H:%M"),
                "content": (a.content or "")[:200],
            }
            for a in anns
        ]
    except Exception as e:
        logger.debug(f"Announcement fetch failed {symbol}: {e}")
        return []


class AnnouncementEvalRequest(BaseModel):
    symbol: str
    market: str = "IN"
    model_id: int | None = None


@router.post("/announcement-eval")
async def announcement_eval(req: AnnouncementEvalRequest, db: Session = Depends(get_db)):
    """Recent announcements -> AI rates each positive/negative/neutral with one sentence. Falls back to titles without full text."""
    market = _parse_market(req.market).value
    cache_key = f"{market}:{req.symbol}"
    cached = _ANN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    stock = db.query(Stock).filter(Stock.symbol == req.symbol).first()
    name = stock.name if stock else req.symbol
    anns = await _fetch_recent_announcements(req.symbol, name)
    if not anns:
        result = {"symbol": req.symbol, "market": market, "items": []}
        _ANN_CACHE.set(cache_key, result, ttl_sec=600)  # short cache when there's no data
        return result

    top = anns[:3]
    listing = "\n".join(
        f"{i + 1}. {a['title']} ({a['time']})" + (f" — {a['content']}" if a["content"] else "")
        for i, a in enumerate(top)
    )
    system_prompt = (
        "You summarise company announcements for an educational research service. For each "
        "announcement, say whether it is positive, negative or neutral for the company's "
        "business (not for any investor's position) and give a one-sentence factual reason. "
        "Use only the information given. Never suggest trading, price levels or quantities. "
        "Write in English. Output exactly one line per announcement in the format: "
        "number|positive or negative or neutral|one sentence"
    )
    user_content = f"Recent announcements for {name} ({market}:{req.symbol}):\n{listing}"
    try:
        content = await get_configured_failover_client(db, req.model_id).chat(
            system_prompt, user_content, temperature=0.2
        )
    except Exception as e:
        raise HTTPException(502, f"AI announcement analysis failed: {e}")

    tone_map: dict[int, tuple[str, str]] = {}
    for line in (content or "").splitlines():
        parts = line.split("|")
        idx_raw = parts[0].strip().rstrip(".) ") if parts else ""
        if len(parts) >= 3 and idx_raw.isdigit():
            tone_map[int(idx_raw) - 1] = (_parse_tone(parts[1]), parts[2].strip())

    items = []
    for i, a in enumerate(top):
        tone, note = tone_map.get(i, ("Neutral", ""))
        items.append(
            {
                "title": ensure_guarded(a["title"], surface="announcement_title"),
                "time": a["time"],
                "tone": tone,
                "summary": ensure_guarded(note, surface="announcement_summary"),
            }
        )
    result = {"symbol": req.symbol, "market": market, "items": items}
    _ANN_CACHE.set(cache_key, result)
    return result
