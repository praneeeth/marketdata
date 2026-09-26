"""News API."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
from src.platform.marketdata.collectors.news_collector import ANNOUNCEMENT_SOURCE, NewsCollector, NewsItem

router = APIRouter()

# Source display names (news sources arrive in Phase 4a; unknown sources show their raw id)
SOURCE_LABELS: dict[str, str] = {}


class NewsItemResponse(BaseModel):
    source: str
    source_label: str
    external_id: str
    title: str
    content: str
    publish_time: str
    symbols: list[str]
    importance: int
    url: str = ""


@router.get("", response_model=list[NewsItemResponse])
async def get_news(
    symbols: str = Query(default="", description="Stock symbols, comma-separated"),
    names: str = Query(default="", description="Stock names, comma-separated (preferred; more stable than symbols)"),
    hours: int = Query(default=168, ge=1, le=720, description="Time window in hours (default 7 days)"),
    limit: int = Query(default=50, ge=1, le=200, description="Number of items"),
    filter_related: bool = Query(default=True, description="Only show related news"),
    source: str = Query(default="", description="Source filter, comma-separated"),
    db: Session = Depends(get_db),
):
    """
    List news.

    - symbols: symbol filter, comma-separated; empty means news for every watchlist stock
    - names: stock name filter, comma-separated (the frontend passes names directly; more stable)
    - hours: time window
    - limit: maximum number of items
    - filter_related: only news about watchlist stocks
    """
    # All watchlist stocks (for matching)
    all_stocks = db.query(Stock).all()
    stock_map = {s.symbol: s.name for s in all_stocks}
    name_to_symbol = {s.name: s.symbol for s in all_stocks}

    # Resolve stocks, preferring the names parameter
    if names:
        # The frontend passes stock names directly
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        # Convert to a symbol list (for matching and the response)
        symbol_list = [name_to_symbol.get(n) for n in name_list if name_to_symbol.get(n)]
        # Build symbol_names from the given names
        passed_symbol_names = {name_to_symbol.get(n, ""): n for n in name_list if name_to_symbol.get(n)}
    elif symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        passed_symbol_names = {s: stock_map.get(s, s) for s in symbol_list}
    else:
        symbol_list = list(stock_map.keys())
        passed_symbol_names = stock_map

    if not symbol_list:
        return []

    source_filters = {s.strip() for s in source.split(",") if s.strip()} if source else set()

    # Match keywords (symbols + names)
    keywords = set(symbol_list)
    for sym in symbol_list:
        if sym in stock_map:
            keywords.add(stock_map[sym])

    # Build the collector, passing the symbol-name map to avoid another DB query
    collector = NewsCollector.from_database()
    news_items = await collector.fetch_all(
        symbols=symbol_list,
        since_hours=hours,
        symbol_names=passed_symbol_names,  # pass the existing symbol-name map
    )

    def is_related(item: NewsItem) -> bool:
        """Whether an item is about a watchlist stock."""
        # Announcements are always stock-related
        if item.source == ANNOUNCEMENT_SOURCE:
            return True
        # Already tagged with related stocks
        if item.symbols and any(s in symbol_list for s in item.symbols):
            return True
        # Title or content contains a keyword
        text = item.title + (item.content or "")
        return any(kw in text for kw in keywords)

    result = []
    for item in news_items:
        if source_filters and item.source not in source_filters:
            continue
        # Drop unrelated news
        if filter_related and not is_related(item):
            continue

        # Tag the matched stocks
        matched_symbols = []
        text = item.title + (item.content or "")
        for sym, name in stock_map.items():
            if sym in symbol_list and (sym in text or name in text):
                matched_symbols.append(sym)

        result.append(NewsItemResponse(
            source=item.source,
            source_label=SOURCE_LABELS.get(item.source, item.source),
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            publish_time=item.publish_time.strftime("%Y-%m-%d %H:%M"),
            symbols=matched_symbols or item.symbols,
            importance=item.importance,
            url=item.url,
        ))

        if len(result) >= limit:
            break

    return result


@router.get("/sources")
def get_news_sources():
    """Configured news sources. None until Indian news and filings sources land (Phase 4a)."""
    return []
