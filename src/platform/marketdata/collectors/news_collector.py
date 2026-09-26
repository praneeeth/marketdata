"""News item type and a thin collector used by agents and the news API.

The upstream Chinese news sources (Xueqiu, Eastmoney, CLS) were removed in Phase 2.
Indian news and filings sources arrive in Phase 4a; until then ``fetch_all`` returns
nothing and callers show "no news".
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    """One news item or corporate announcement."""

    source: str  # provider id
    external_id: str  # provider-side unique id
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)  # related symbols
    importance: int = 0  # 0-3
    url: str = ""


class NewsCollector:
    @classmethod
    def from_database(cls) -> "NewsCollector":
        return cls()

    async def fetch_all(
        self,
        symbols: list[str] | None = None,
        since_hours: int = 2,
        symbol_names: dict[str, str] | None = None,
    ) -> list[NewsItem]:
        """Recent news for ``symbols``, newest first. Empty until Phase 4a."""
        from src.platform.marketdata.marketdata_client import md_news

        return await asyncio.to_thread(md_news, symbols or [], since_hours, symbol_names)
