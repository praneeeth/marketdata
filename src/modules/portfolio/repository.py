"""Portfolio persistence queries used by module services."""

from __future__ import annotations

from sqlalchemy.orm import Session

# The tables are registered by the shared persistence platform; the portfolio repository uses them directly instead of keeping
# a ``portfolio.models`` re-export file with no domain behaviour.
from src.platform.persistence.models import PaperTradingPosition, Position, Stock


class PortfolioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_real_positions_with_stocks(self) -> list[tuple[Position, Stock]]:
        return (
            self._session.query(Position, Stock)
            .join(Stock, Position.stock_id == Stock.id)
            .order_by(Stock.sort_order.asc(), Position.id.asc())
            .all()
        )

    def list_open_paper_positions(self) -> list[PaperTradingPosition]:
        return (
            self._session.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
