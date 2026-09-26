"""Context maintenance scheduler: outcome evaluation + stale data cleanup + automatic opportunity refresh."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.platform.compliance import Feature, is_feature_enabled
from src.platform.marketdata.collectors.kline_collector import kline_source
from src.modules.research.context_store import cleanup_context_data
from src.modules.strategy.entry_candidates import evaluate_entry_candidate_outcomes
from src.modules.research.prediction_outcome import evaluate_pending_prediction_outcomes
from src.modules.strategy.strategy_engine import (
    evaluate_strategy_outcomes,
    rebalance_strategy_weights,
    refresh_strategy_signals,
)

logger = logging.getLogger(__name__)


class ContextMaintenanceScheduler:
    def __init__(
        self,
        timezone: str = "UTC",
        eval_interval_hours: int = 6,
        snapshot_retention_days: int = 180,
        outcome_retention_days: int = 365,
    ):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.eval_interval_hours = max(1, int(eval_interval_hours))
        self.snapshot_retention_days = max(30, int(snapshot_retention_days))
        self.outcome_retention_days = max(60, int(outcome_retention_days))
        self._evaluating = False
        self._cleaning = False
        self._refreshing = False

    async def _evaluate_job(self):
        if self._evaluating:
            logger.debug("[Context maintenance] previous outcome evaluation still running; skipping this round")
            return
        self._evaluating = True
        try:
            stats = await asyncio.to_thread(evaluate_pending_prediction_outcomes)
            level = logging.INFO if stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[Context maintenance] outcome evaluation done: pending=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                stats.get("total_pending", 0),
                stats.get("eligible", 0),
                stats.get("evaluated", 0),
                stats.get("skipped_not_due", 0),
                stats.get("skipped_no_price", 0),
            )
            with kline_source("outcome_eval"):
                cand_stats = await asyncio.to_thread(
                    evaluate_entry_candidate_outcomes,
                    horizons=(1, 3, 5, 10),
                    snapshot_days=45,
                    limit=500,
                )
            level = logging.INFO if cand_stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[Context maintenance] candidate outcome evaluation done: total=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                cand_stats.get("total_candidates", 0),
                cand_stats.get("eligible", 0),
                cand_stats.get("evaluated", 0),
                cand_stats.get("skipped_not_due", 0),
                cand_stats.get("skipped_no_price", 0),
            )
            with kline_source("outcome_eval"):
                strategy_stats = await asyncio.to_thread(
                    evaluate_strategy_outcomes,
                    horizons=(1, 3, 5, 10),
                    snapshot_days=60,
                    limit=1200,
                )
            level = logging.INFO if strategy_stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[Context maintenance] strategy outcome evaluation done: total=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                strategy_stats.get("total_signals", 0),
                strategy_stats.get("eligible", 0),
                strategy_stats.get("evaluated", 0),
                strategy_stats.get("skipped_not_due", 0),
                strategy_stats.get("skipped_no_price", 0),
            )
            rebalance = await asyncio.to_thread(
                rebalance_strategy_weights,
                window_days=45,
                min_samples=8,
                alpha=0.35,
                regime="default",
            )
            level = logging.INFO if rebalance.get("changed", 0) else logging.DEBUG
            logger.log(
                level,
                "[Context maintenance] strategy re-weighting done: changed=%s checked=%s skipped_low_sample=%s",
                rebalance.get("changed", 0),
                rebalance.get("checked", 0),
                rebalance.get("skipped_low_sample", 0),
            )

            # Phase 4 -> factor self-calibration loop: feeds IC/IR into a light calibration of per-factor weights
            # (calibrate_all_markets computes IC per market and re-weights from it, instead of only recording it).
            try:
                from src.modules.strategy.factor_calibration import calibrate_all_markets

                fcal = await asyncio.to_thread(calibrate_all_markets)
                changed = sum(r.get("changed", 0) for r in fcal.values())
                logger.log(
                    logging.INFO if changed else logging.DEBUG,
                    "[Context maintenance] factor self-calibration done: changed=%s detail=%s",
                    changed,
                    {m: r.get("changed", 0) for m, r in fcal.items()},
                )
            except Exception as fc_err:
                logger.debug("[Context maintenance] factor self-calibration skipped: %s", fc_err)
        except Exception as e:
            logger.exception(f"[Context maintenance] outcome evaluation error: {e}")
        finally:
            self._evaluating = False

    async def _cleanup_job(self):
        if self._cleaning:
            logger.debug("[Context maintenance] previous cleanup still running; skipping this round")
            return
        self._cleaning = True
        try:
            deleted = await asyncio.to_thread(
                cleanup_context_data,
                snapshot_days=self.snapshot_retention_days,
                topic_days=self.snapshot_retention_days,
                context_run_days=self.snapshot_retention_days,
                outcome_days=self.outcome_retention_days,
            )
            # deleted is a dict; any field > 0 means something was cleaned up
            try:
                from src.platform.compliance.audit import purge_old_events

                if isinstance(deleted, dict):
                    deleted["compliance_events"] = await asyncio.to_thread(purge_old_events)
            except Exception:
                logger.exception("[Context maintenance] compliance_events cleanup failed")
            has_work = bool(deleted and any(deleted.values()) if isinstance(deleted, dict) else deleted)
            level = logging.INFO if has_work else logging.DEBUG
            logger.log(level, "[Context maintenance] cleanup done: %s", deleted)
        except Exception as e:
            logger.exception(f"[Context maintenance] cleanup error: {e}")
        finally:
            self._cleaning = False

    async def evaluate_once(self) -> dict:
        agent_task = asyncio.to_thread(evaluate_pending_prediction_outcomes)
        candidate_task = asyncio.to_thread(
            evaluate_entry_candidate_outcomes,
            horizons=(1, 3, 5, 10),
            snapshot_days=45,
            limit=500,
        )
        strategy_eval_task = asyncio.to_thread(
            evaluate_strategy_outcomes,
            horizons=(1, 3, 5, 10),
            snapshot_days=60,
            limit=1200,
        )
        strategy_rebalance_task = asyncio.to_thread(
            rebalance_strategy_weights,
            window_days=45,
            min_samples=8,
            alpha=0.35,
            regime="default",
        )
        agent_stats, candidate_stats, strategy_eval_stats, strategy_rebalance_stats = await asyncio.gather(
            agent_task,
            candidate_task,
            strategy_eval_task,
            strategy_rebalance_task,
        )
        # Factor self-calibration must run after outcome evaluation (so IC is fresh); it can't join the gather above.
        from src.modules.strategy.factor_calibration import calibrate_all_markets

        factor_calibration_stats = await asyncio.to_thread(calibrate_all_markets)
        return {
            "agent_predictions": agent_stats,
            "entry_candidates": candidate_stats,
            "strategy_outcomes": strategy_eval_stats,
            "strategy_rebalance": strategy_rebalance_stats,
            "factor_calibration": factor_calibration_stats,
        }

    async def _refresh_opportunities_job(self):
        """Refresh the opportunity pool on a schedule (candidates + strategy signals). Skipped on market holidays."""
        from src.platform.scheduling.trading_calendar import any_market_trading_day

        if not any_market_trading_day():
            logger.debug("[Context maintenance] not a trading day; skipping the opportunity refresh")
            return
        if self._refreshing:
            logger.debug("[Context maintenance] previous opportunity refresh still running; skipping this round")
            return
        self._refreshing = True
        try:
            with kline_source("refresh_opportunities"):
                result = await asyncio.to_thread(
                    refresh_strategy_signals,
                    rebuild_candidates=True,
                    max_inputs=500,
                    market_scan_limit=80,
                    max_kline_symbols=60,
                    limit_candidates=2000,
                )
            level = logging.INFO if result.get("count", 0) else logging.DEBUG
            logger.log(
                level,
                "[Context maintenance] automatic opportunity refresh done: snapshot_date=%s count=%s",
                result.get("snapshot_date", ""),
                result.get("count", 0),
            )
        except Exception as e:
            logger.exception(f"[Context maintenance] automatic opportunity refresh error: {e}")
        finally:
            self._refreshing = False

    async def refresh_opportunities_once(self) -> dict:
        """Trigger one opportunity refresh manually."""
        with kline_source("refresh_opportunities"):
            return await asyncio.to_thread(
                refresh_strategy_signals,
                rebuild_candidates=True,
                max_inputs=500,
                market_scan_limit=80,
                max_kline_symbols=60,
                limit_candidates=2000,
            )

    async def cleanup_once(self) -> dict:
        return await asyncio.to_thread(
            cleanup_context_data,
            snapshot_days=self.snapshot_retention_days,
            topic_days=self.snapshot_retention_days,
            context_run_days=self.snapshot_retention_days,
            outcome_days=self.outcome_retention_days,
        )

    async def _refresh_trading_calendar_job(self):
        """Refresh the NSE/BSE trading calendar daily.

        A loaded calendar only covers the current year, so a long-running instance would fall back to "weekends only"
        after the new year; it is refreshed early every morning, before any pre-market notification, so each day uses a fresh calendar.
        """
        from src.platform.scheduling.trading_calendar import refresh

        try:
            await refresh()
        except Exception as e:  # refresh already catches its own errors; this only guards against surprises
            logger.exception(f"[Context maintenance] trading calendar refresh error: {e}")

    def start(self):
        self.scheduler.add_job(
            self._evaluate_job,
            "interval",
            hours=self.eval_interval_hours,
            jitter=120,  # staggered so it doesn't write SQLite at the same moment as price_alert/paper_trading (60s)
            id="context_maintenance_evaluate",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._cleanup_job,
            "cron",
            hour=4,
            minute=15,
            jitter=120,
            id="context_maintenance_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # Daily trading calendar refresh at 03:00, before every pre-market notification
        self.scheduler.add_job(
            self._refresh_trading_calendar_job,
            "cron",
            hour=3,
            minute=0,
            jitter=120,
            id="context_maintenance_trading_calendar",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # Automatic opportunity refresh: 09:15 pre-open / 13:30 midday / 22:00 evening.
        # Times use the scheduler time zone (app_timezone, default Asia/Kolkata), the same as agent cron.
        # Research-only: the opportunity (entry-candidate) engine does not run (ADR-004).
        refresh_times = (
            ((9, 15), (13, 30), (22, 0)) if is_feature_enabled(Feature.ENTRY_CANDIDATES) else ()
        )
        for job_hour, job_minute in refresh_times:
            self.scheduler.add_job(
                self._refresh_opportunities_job,
                "cron",
                hour=job_hour,
                minute=job_minute,
                jitter=120,  # staggered so it doesn't write SQLite at the same moment as other schedules
                id=f"context_maintenance_refresh_opportunities_{job_hour:02d}{job_minute:02d}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Run a bootstrap evaluation shortly after startup to warm up outcome stats.
        self.scheduler.add_job(
            self._evaluate_job,
            "date",
            run_date=datetime.now(self.scheduler.timezone) + timedelta(seconds=15),
            id="context_maintenance_bootstrap_evaluate",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("context", self.scheduler)
        logger.info(
            "Context maintenance scheduler started (outcome evaluation every %sh, catch-up run at +15s, snapshots kept %s days, outcomes kept %s days, opportunity refresh 09:15/13:30/22:00, trading calendar refresh 03:00)",
            self.eval_interval_hours,
            self.snapshot_retention_days,
            self.outcome_retention_days,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("Context maintenance scheduler stopped")
