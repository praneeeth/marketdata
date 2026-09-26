"""Simulation scheduler: scans for entries/exits every 60 seconds."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.paper_trading.paper_trading_engine import ENGINE
from src.platform.scheduling.trading_calendar import any_market_trading_day
from src.platform.marketdata.models import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def _any_market_trading() -> bool:
    """True while NSE/BSE are in session; outside it quotes don't move, so scans can skip."""
    return MARKETS[MarketCode.IN].is_trading_time()


class PaperTradingScheduler:
    def __init__(self, timezone: str = "UTC", interval_seconds: int = 60):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.interval_seconds = max(15, int(interval_seconds))
        self._running = False

    async def _scan_job(self):
        if self._running:
            logger.debug("[Simulation] previous scan still running; skipping this round")
            return
        if not _any_market_trading():
            logger.debug("[Simulation] market closed; skipping this scan")
            return
        self._running = True
        try:
            result = await ENGINE.scan_once()
            opened = result.get("opened", 0)
            closed = result.get("closed", 0)
            status = result.get("status", "?")
            # Only real entries/exits are business events; otherwise it's just a heartbeat.
            level = logging.INFO if (opened or closed) else logging.DEBUG
            logger.log(
                level,
                "[Simulation] scan done: opened=%s closed=%s status=%s",
                opened,
                closed,
                status,
            )
        except Exception as e:
            logger.exception(f"[Simulation] scan error: {e}")
        finally:
            self._running = False

    async def _premarket_job(self):
        """Pre-market plan notification. Skipped on non-trading days (weekends/holidays)."""
        if not any_market_trading_day():
            logger.debug("[Simulation] not a trading day; skipping the pre-market plan notification")
            return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_premarket_plan
            await send_premarket_plan()
        except Exception as e:
            logger.exception(f"[Simulation] pre-market plan notification error: {e}")

    async def _summary_job(self):
        """End-of-day summary notification. Skipped on non-trading days (weekends/holidays)."""
        if not any_market_trading_day():
            logger.debug("[Simulation] not a trading day; skipping the end-of-day summary notification")
            return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_daily_summary
            await send_daily_summary()
        except Exception as e:
            logger.exception(f"[Simulation] end-of-day summary notification error: {e}")

    def start(self):
        self.scheduler.add_job(
            self._scan_job,
            "interval",
            seconds=self.interval_seconds,
            jitter=20,  # jitter so it doesn't write SQLite at the same moment as the 60s price alert scan
            id="paper_trading_scan",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # Pre-market plan: 09:00 every day
        self.scheduler.add_job(
            self._premarket_job,
            "cron",
            hour=9,
            minute=0,
            id="paper_trading_premarket",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # End-of-day summary: 15:30 every day
        self.scheduler.add_job(
            self._summary_job,
            "cron",
            hour=15,
            minute=30,
            id="paper_trading_summary",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("paper_trading", self.scheduler)
        logger.info(f"Simulation scheduler started; scan interval {self.interval_seconds}s")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("Simulation scheduler stopped")
