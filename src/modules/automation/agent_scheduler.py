import logging
import time
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.automation.base import BaseAgent, AgentContext
from src.platform.marketdata.collectors.kline_collector import kline_source
from src.modules.automation.agent_runs import record_agent_run
from src.platform.observability.log_context import log_context
from src.platform.observability import otel
from src.platform.marketdata.models import MARKETS
from src.platform.scheduling.schedule_parser import parse_schedule

logger = logging.getLogger(__name__)


class AgentScheduler:
    """Agent scheduler."""

    def __init__(self, timezone: str = "UTC"):
        self.scheduler = AsyncIOScheduler()
        self.agents: dict[str, BaseAgent] = {}
        self.execution_modes: dict[str, str] = {}
        self.timezone = timezone
        # Stores a context builder instead of a fixed context
        self.context_builder: Callable[[str], AgentContext] | None = None

    def set_context_builder(self, builder: Callable[[str], AgentContext]):
        """Set the context builder (the context is built fresh on every run)."""
        self.context_builder = builder

    def register(self, agent: BaseAgent, schedule: str, execution_mode: str = "batch"):
        """
        Register an agent with the scheduler.

        Args:
            agent: agent instance
            schedule: schedule expression
                - cron format: "minute hour day month weekday" (5 parts)
                - interval format: "interval:3m" or "interval:30s"
            execution_mode: run mode batch/single (single runs run_single per stock)
        """
        self.agents[agent.name] = agent
        self.execution_modes[agent.name] = execution_mode or "batch"

        # Parse the schedule expression
        # cron has 5 fields: "minute hour day month weekday"
        # day_of_week numbers follow POSIX cron (1-5 = Monday to Friday) and are normalised internally.
        trigger = parse_schedule(schedule, timezone=self.timezone)

        self.scheduler.add_job(
            self._run_agent,
            trigger=trigger,
            args=[agent.name],
            id=agent.name,
            name=agent.display_name,
            replace_existing=True,
        )

        logger.info(f"Registered agent: {agent.display_name} (schedule: {schedule})")

    # NOTE: cron/interval parsing lives in src/core/schedule_parser.py

    async def _run_agent(self, agent_name: str):
        """Run the given agent (building the context fresh)."""
        if not self.context_builder:
            logger.error("context_builder not set")
            return

        agent = self.agents.get(agent_name)
        if not agent:
            logger.error(f"Agent not found: {agent_name}")
            return

        start = time.monotonic()
        trace_id = f"sch-{agent_name}-{int(time.time() * 1000)}"
        try:
            # OTel root span (no-op when disabled); shares the in-house trace's trace_id.
            with otel.agent_run_span(
                agent_name, trace_id=trace_id, trigger_source="schedule"
            ), log_context(
                trace_id=trace_id,
                run_id=trace_id,
                agent_name=agent_name,
                event="agent_run",
                tags={"trigger_source": "schedule"},
            ):
                # Build the context fresh on every run (latest config)
                context = self.context_builder(agent_name)
                logger.info(f"[Scheduler] running agent: {agent.display_name}")
                mode = self.execution_modes.get(agent_name, "batch")
                if mode == "single" and hasattr(agent, "run_single"):
                    processed = 0
                    skipped = 0
                    errors: list[str] = []
                    for stock in list(context.watchlist):
                        market_def = MARKETS.get(stock.market)
                        if market_def and not market_def.is_trading_time():
                            skipped += 1
                            logger.info(
                                f"[Scheduler] skipping {agent.display_name} {stock.symbol} ({market_def.name} outside trading hours)"
                            )
                            continue
                        try:
                            with kline_source(f"agent:{agent_name}"):
                                res = await agent.run_single(context, stock.symbol)  # type: ignore[attr-defined]
                            processed += 1
                            try:
                                notify_error = (
                                    (res.raw_data or {}).get("notify_error")
                                    if res
                                    else ""
                                )
                            except Exception:
                                notify_error = ""
                            if notify_error:
                                errors.append(f"{stock.symbol} notify: {notify_error}")
                        except Exception as e:
                            logger.error(
                                f"Agent [{agent_name}] single-stock run failed {stock.symbol}: {e}",
                                exc_info=True,
                            )
                            errors.append(f"{stock.symbol}: {e}")
                    logger.info(
                        f"[Scheduler] agent single-stock mode done: {agent.display_name} (ran {processed}, skipped {skipped}, total {len(context.watchlist)})"
                    )
                    duration_ms = int((time.monotonic() - start) * 1000)
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if errors else "success",
                        result=f"single mode executed {processed}, skipped {skipped}, total {len(context.watchlist)}",
                        error="; ".join(errors),
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        model_label=context.model_label,
                    )
                else:
                    with kline_source(f"agent:{agent_name}"):
                        result = await agent.run(context)
                    duration_ms = int((time.monotonic() - start) * 1000)
                    notify_error = ""
                    try:
                        notify_error = (result.raw_data or {}).get("notify_error") or ""
                    except Exception:
                        notify_error = ""
                    raw = result.raw_data or {}
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if notify_error else "success",
                        result=(result.content or "")[:2000],
                        error=(notify_error or "")[:2000],
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        notify_attempted=(
                            "notified" in raw
                            or "notify_error" in raw
                            or "notify_skipped" in raw
                        ),
                        notify_sent=bool(raw.get("notified", False)),
                        model_label=context.model_label,
                    )
                logger.info(f"[Scheduler] agent done: {agent.display_name}")
        except Exception as e:
            logger.error(f"Agent [{agent_name}] scheduled run error: {e}", exc_info=True)
            duration_ms = int((time.monotonic() - start) * 1000)
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=duration_ms,
                trace_id=trace_id,
                trigger_source="schedule",
            )

    async def trigger_now(self, agent_name: str):
        """Run an agent now (manual trigger)."""
        await self._run_agent(agent_name)

    def start(self):
        """Start the scheduler."""
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("agent", self.scheduler)
        logger.info(f"Scheduler started with {len(self.agents)} agents registered")

        # List all registered jobs
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            logger.info(f"  - {job.name}: next run {job.next_run_time}")

    def shutdown(self):
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")
