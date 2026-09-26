"""Candlewise service entry point: web backend + agent scheduling."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn

from src.platform.persistence.database import init_db, SessionLocal
from src.platform.persistence.models import (
    AgentConfig,
    Stock,
    StockAgent,
    AIService,
    AIModel,
    NotifyChannel,
    AppSettings,
)
from src.platform.observability.log_handler import DBLogHandler
from src.platform.runtime.config import Settings, AppConfig, StockConfig
from src.platform.marketdata.models import MarketCode
from src.platform.ai.ai_client import AIClient
from src.platform.ai.ai_failover import build_failover_client
from src.platform.notifications.notifier import NotifierManager
from src.modules.automation.agent_scheduler import AgentScheduler
from src.modules.market.price_alert_scheduler import PriceAlertScheduler
from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler
from src.modules.research.context_scheduler import ContextMaintenanceScheduler
from src.modules.automation.agent_runs import record_agent_run
from src.platform.observability.log_context import install_log_record_factory, log_context
from src.modules.automation.agent_catalog import (
    AGENT_SEED_SPECS,
    AGENT_KIND_WORKFLOW,
)
from src.modules.strategy.strategy_catalog import ensure_strategy_catalog
from src.modules.automation.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.news_digest import NewsDigestAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.automation.tradingagents import TradingAgentsAgent

logger = logging.getLogger(__name__)

# Global scheduler instance, used by the agents API
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None
paper_trading_scheduler: PaperTradingScheduler | None = None
context_maintenance_scheduler: ContextMaintenanceScheduler | None = None


def apply_proxy_env(proxy: str | None) -> None:
    """Set the proxy in the process environment so every default httpx Client (trust_env=True) uses it.

    An empty string / None clears the variables (no proxy).
    NO_PROXY includes localhost / loopback by default, so local calls don't take a detour.
    """
    p = (proxy or "").strip()
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        logger.info(f"HTTP/HTTPS proxy applied: {p}")
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        logger.info("HTTP/HTTPS proxy cleared")


def setup_proxy():
    """At startup, bridge the configured HTTP proxy into environment variables.

    Priority:
    1. existing HTTP_PROXY / HTTPS_PROXY environment variables (an explicit user override; left alone)
    2. app_settings.http_proxy (UI setting)
    3. http_proxy in .env (Settings.http_proxy)
    """
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        logger.info(
            f"Keeping the existing environment proxy: HTTP_PROXY={os.environ.get('HTTP_PROXY', '')} "
            f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY', '')}"
        )
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        return

    proxy = ""
    try:
        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            if setting and setting.value:
                proxy = setting.value.strip()
        finally:
            db.close()
    except Exception:
        pass

    if not proxy:
        proxy = (Settings().http_proxy or "").strip()

    if proxy:
        apply_proxy_env(proxy)


def setup_ssl():
    """Set up the SSL certificate environment (corporate proxy environments)."""
    settings = Settings()
    ca_cert = settings.ca_cert_file
    if not ca_cert or not os.path.exists(ca_cert):
        return

    import certifi

    bundle_path = os.path.join(os.path.dirname(__file__), "data", "ca-bundle.pem")
    os.makedirs(os.path.dirname(bundle_path), exist_ok=True)

    need_rebuild = not os.path.exists(bundle_path) or os.path.getmtime(
        ca_cert
    ) > os.path.getmtime(bundle_path)

    if need_rebuild:
        with open(bundle_path, "w") as out:
            with open(certifi.where(), "r") as f:
                out.write(f.read())
            out.write("\n")
            with open(ca_cert, "r") as f:
                out.write(f.read())

    os.environ["SSL_CERT_FILE"] = bundle_path
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_path
    logger.info(f"SSL certificates loaded: {bundle_path}")


def setup_logging():
    """Configure logging: console + database.

    Levels:
    - the root logger is always DEBUG, so every log reaches the handlers
    - the console handler filters by LOG_LEVEL (INFO by default) and drops < WARNING noise from third-party libraries such as httpx
    - the DB handler always records everything at DEBUG, so the UI log board shows the full record including heartbeats/httpx requests
    """
    console_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    console_level = getattr(logging, console_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    install_log_record_factory()

    # Avoid duplicate handlers (and multiplied logs) on reload/server restart.
    for h in list(root.handlers):
        if isinstance(h, DBLogHandler) or getattr(h, "_candlewise_console", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # Console output: filtered by LOG_LEVEL, dropping low-level third-party noise
    console = logging.StreamHandler()
    console._candlewise_console = True  # type: ignore[attr-defined]
    console.setLevel(console_level)
    console.addFilter(_ConsoleNoiseFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # Database persistence: records everything, so the UI log board can show DEBUG
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)

    # uvicorn attaches its own stderr handler with propagate=False, so its access log
    # (`INFO: 127.0.0.1 - "GET /api/..."`) takes its own path and bypasses our filter.
    # Clear its handlers and propagate to root so _ConsoleNoiseFilter applies.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)


class _ConsoleNoiseFilter(logging.Filter):
    """Console handler filter: third-party INFO/DEBUG stays out of stdout; WARNING+ still shows.
    The DB handler doesn't use this filter, so the UI log board sees the full request record.

    uvicorn.access is the per-request access log (`INFO: 127.0.0.1 - "GET /api/..." 200 OK`),
    low-level heartbeat noise; uvicorn / uvicorn.error are app-level logs (startup, errors) and are kept."""

    _NOISY_PREFIXES = ("httpx", "httpcore", "urllib3", "apscheduler", "uvicorn.access")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        name = record.name or ""
        for prefix in self._NOISY_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                return False
        return True


def seed_sample_stocks():
    """Add a few NSE large caps on first start so the watchlist isn't empty."""
    db = SessionLocal()
    try:
        # Only add samples when there are no stocks at all
        if db.query(Stock).count() > 0:
            return

        samples = [
            {"symbol": "RELIANCE", "name": "Reliance Industries", "market": "IN"},
            {"symbol": "TCS", "name": "Tata Consultancy Services", "market": "IN"},
            {"symbol": "HDFCBANK", "name": "HDFC Bank", "market": "IN"},
            {"symbol": "INFY", "name": "Infosys", "market": "IN"},
            {"symbol": "ICICIBANK", "name": "ICICI Bank", "market": "IN"},
        ]
        for s in samples:
            db.add(Stock(**s))
        db.commit()
        logger.info("Added 5 sample NSE stocks (first start)")
    finally:
        db.close()


def seed_agents():
    """Initialise the built-in agent configs."""
    db = SessionLocal()
    for spec in AGENT_SEED_SPECS:
        existing = db.query(AgentConfig).filter(AgentConfig.name == spec.name).first()
        if not existing:
            db.add(
                AgentConfig(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    kind=spec.kind,
                    visible=spec.visible,
                    lifecycle_status=spec.lifecycle_status,
                    replaced_by=spec.replaced_by,
                    display_order=spec.display_order,
                    enabled=spec.enabled,
                    schedule=spec.schedule,
                    execution_mode=spec.execution_mode,
                    config=spec.config or {},
                )
            )
        else:
            # Always sync execution_mode (so the definition in code wins)
            existing.execution_mode = spec.execution_mode or "batch"
            # Sync display_name and description
            existing.display_name = spec.display_name or existing.display_name
            existing.description = spec.description or existing.description
            existing.kind = spec.kind
            existing.visible = bool(spec.visible)
            existing.lifecycle_status = spec.lifecycle_status or "active"
            existing.replaced_by = spec.replaced_by or ""
            existing.display_order = int(spec.display_order or 0)

            # Capabilities are never scheduled, so old configs can't trigger them.
            if spec.kind != AGENT_KIND_WORKFLOW:
                existing.enabled = False
                existing.schedule = ""

            # Fill in the default config only when the user hasn't set one
            if spec.config and (not existing.config):
                existing.config = spec.config
            # Forward-compatible: fill in missing fields of an existing config (without overwriting the user's values)
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


def seed_strategies():
    """Initialise the strategy catalogue."""
    ensure_strategy_catalog()
    logger.info("Strategy catalogue initialised")


def load_watchlist_for_agent(agent_name: str) -> list[StockConfig]:
    """Load the watchlist stocks linked to an agent from the database."""
    db = SessionLocal()
    try:
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = [sa.stock_id for sa in stock_agents]
        if not stock_ids:
            return []

        # Binding wins: any stock bound to the agent is included
        stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
        result = []
        for s in stocks:
            try:
                market = MarketCode(s.market)
            except ValueError:
                market = MarketCode.IN
            result.append(
                StockConfig(
                    symbol=s.symbol,
                    name=s.name,
                    market=market,
                )
            )
        return result
    finally:
        db.close()


def load_portfolio_for_agent(agent_name: str) -> PortfolioInfo:
    """Load the positions of an agent's linked stocks from the database (across accounts)."""
    from src.platform.persistence.models import Account, Position

    db = SessionLocal()
    try:
        # IDs of the stocks linked to the agent
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = set(sa.stock_id for sa in stock_agents)
        if not stock_ids:
            return PortfolioInfo()

        # All enabled accounts
        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            # The account's positions in the linked stocks
            positions = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id.in_(stock_ids),
                )
                .all()
            )

            position_infos = []
            for pos in positions:
                stock = pos.stock
                if not stock:
                    continue
                try:
                    market = MarketCode(stock.market)
                except ValueError:
                    market = MarketCode.IN

                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def load_portfolio_for_stock(stock_id: int) -> PortfolioInfo:
    """Load one stock's positions from the database."""
    from src.platform.persistence.models import Account, Position

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return PortfolioInfo()

        try:
            market = MarketCode(stock.market)
        except ValueError:
            market = MarketCode.IN

        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id == stock_id,
                )
                .first()
            )

            position_infos = []
            if pos:
                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def _get_proxy() -> str:
    """Get http_proxy from app_settings."""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def _get_app_setting(key: str) -> str:
    """Get a setting from app_settings (empty string when missing)."""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == key).first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def resolve_ai_model(
    agent_name: str, stock_agent_id: int | None = None
) -> tuple[AIModel | None, AIService | None]:
    """Resolve the AI model: stock_agent override -> agent default -> system default (is_default=True).
    Returns a (model, service) tuple."""
    db = SessionLocal()
    try:
        model_id = None

        # 1. stock_agent-level override
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.ai_model_id:
                model_id = sa.ai_model_id

        # 2. agent-level default
        if not model_id:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.ai_model_id:
                model_id = agent.ai_model_id

        # 3. system default
        if not model_id:
            default_model = db.query(AIModel).filter(AIModel.is_default == True).first()
            if default_model:
                model_id = default_model.id

        # 4. fallback: the first one
        if not model_id:
            first_model = db.query(AIModel).first()
            if first_model:
                model_id = first_model.id

        if not model_id:
            return None, None

        model = db.query(AIModel).filter(AIModel.id == model_id).first()
        if not model:
            return None, None

        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if model:
            db.expunge(model)
        if service:
            db.expunge(service)
        return model, service
    finally:
        db.close()


def resolve_notify_channels(
    agent_name: str, stock_agent_id: int | None = None
) -> list[NotifyChannel]:
    """Resolve notification channels: stock_agent override -> agent default -> system default (is_default=True)."""
    db = SessionLocal()
    try:
        channel_ids = None

        # 1. stock_agent-level override
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.notify_channel_ids:
                channel_ids = sa.notify_channel_ids

        # 2. agent-level default
        if channel_ids is None:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.notify_channel_ids:
                channel_ids = agent.notify_channel_ids

        # 3. by id list, or the system default
        if channel_ids:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.id.in_(channel_ids),
                    NotifyChannel.enabled == True,
                )
                .all()
            )
        else:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.is_default == True,
                    NotifyChannel.enabled == True,
                )
                .all()
            )

        for ch in channels:
            db.expunge(ch)
        return channels
    finally:
        db.close()


def _build_notifier(channels: list[NotifyChannel]) -> NotifierManager:
    """Build a NotifierManager from the resolved channels."""
    settings = Settings()
    # allow UI override via app_settings
    quiet_hours = _get_app_setting("notify_quiet_hours") or settings.notify_quiet_hours
    retry_attempts_raw = _get_app_setting("notify_retry_attempts")
    backoff_raw = _get_app_setting("notify_retry_backoff_seconds")
    overrides_raw = (
        _get_app_setting("notify_dedupe_ttl_overrides")
        or settings.notify_dedupe_ttl_overrides
    )

    try:
        retry_attempts = (
            int(retry_attempts_raw)
            if retry_attempts_raw
            else settings.notify_retry_attempts
        )
    except Exception:
        retry_attempts = settings.notify_retry_attempts
    try:
        retry_backoff_seconds = (
            float(backoff_raw) if backoff_raw else settings.notify_retry_backoff_seconds
        )
    except Exception:
        retry_backoff_seconds = settings.notify_retry_backoff_seconds

    from src.platform.notifications.notify_policy import NotifyPolicy, parse_dedupe_overrides

    policy = NotifyPolicy(
        timezone=settings.app_timezone,
        quiet_hours=quiet_hours,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        dedupe_ttl_overrides=parse_dedupe_overrides(overrides_raw),
    )

    notifier = NotifierManager(policy=policy)
    for ch in channels:
        notifier.add_channel(ch.type, ch.config or {})
    return notifier


def _build_ai_client(model: AIModel | None, service: AIService | None, proxy: str):
    """Build an AI client with failover from the resolved model+service.

    The main candidate is the model+service chosen by four-level routing; build_failover_client adds backups from
    the other models in the DB by priority. The returned FailoverAIClient is interface-compatible with AIClient and replaces it in place.
    """
    return build_failover_client(model, service, proxy)


def build_context(agent_name: str, stock_agent_id: int | None = None) -> AgentContext:
    """Build the run context for an agent."""
    settings = Settings()
    watchlist = load_watchlist_for_agent(agent_name)
    portfolio = load_portfolio_for_agent(agent_name)
    proxy = _get_proxy() or settings.http_proxy

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    ai_client = _build_ai_client(model, service, proxy)
    channels = resolve_notify_channels(agent_name, stock_agent_id)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=watchlist)
    return AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        notify_policy=getattr(notifier, "policy", None),
    )


# Agent registry
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    "premarket_outlook": PremarketOutlookAgent,
    "news_digest": NewsDigestAgent,
    "intraday_monitor": IntradayMonitorAgent,
    "tradingagents": TradingAgentsAgent,
}


def build_scheduler() -> AgentScheduler:
    """Build the scheduler and register the enabled agents."""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # Set the context builder (fetches the latest config on every run)
    sched.set_context_builder(build_context)

    db = SessionLocal()
    try:
        agent_configs = (
            db.query(AgentConfig)
            .filter(
                AgentConfig.enabled == True,
                AgentConfig.kind == AGENT_KIND_WORKFLOW,
            )
            .all()
        )
        for cfg in agent_configs:
            agent_cls = AGENT_REGISTRY.get(cfg.name)
            if not agent_cls:
                logger.warning(f"Agent {cfg.name} isn't registered in AGENT_REGISTRY")
                continue
            if not cfg.schedule:
                logger.info(f"Agent {cfg.name} has no schedule; skipping")
                continue

            agent_kwargs = cfg.config or {}
            try:
                agent_instance = (
                    agent_cls(**agent_kwargs) if agent_kwargs else agent_cls()
                )
            except TypeError:
                agent_instance = agent_cls()
            sched.register(
                agent_instance,
                schedule=cfg.schedule,
                execution_mode=cfg.execution_mode or "batch",
            )
    finally:
        db.close()

    return sched


def register_mcp_log_cleanup(sched: AgentScheduler) -> None:
    """Register MCP audit-log retention on the wrapped APScheduler instance.

    ``AgentScheduler`` owns the concrete APScheduler as ``.scheduler``;
    keeping this boundary explicit prevents startup code from accidentally
    calling ``add_job`` on the wrapper itself.
    """
    from src.modules.administration.api.mcp import prune_mcp_logs

    sched.scheduler.add_job(
        prune_mcp_logs,
        "cron",
        hour=4,
        minute=0,
        id="mcp_log_retention",
        replace_existing=True,
    )
    logger.info("MCP log retention cleanup job registered")


def reload_scheduler() -> bool:
    """Reload the scheduler (so config imports / bulk changes take effect at once)."""
    global scheduler
    try:
        current = globals().get("scheduler")
        if current:
            try:
                current.shutdown()
            except Exception:
                pass
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Agent scheduler reloaded")
        return True
    except Exception as e:
        logger.error(f"Agent scheduler reload failed: {e}")
        return False


def _log_trigger_info(
    agent_name: str,
    stocks: list,
    model: AIModel | None,
    service: AIService | None,
    channels: list[NotifyChannel],
):
    """Log the context an agent was triggered with."""
    stock_names = ", ".join(
        f"{s.name}({s.symbol})" if hasattr(s, "symbol") else str(s) for s in stocks
    )
    ai_info = f"{service.name}/{model.model}" if model and service else "not configured"
    channel_info = ", ".join(ch.name for ch in channels) if channels else "none"
    logger.info(
        f"[Trigger] Agent={agent_name} | Stocks=[{stock_names}] | AI={ai_info} | Notify=[{channel_info}]"
    )


def get_agent_execution_mode(agent_name: str) -> str:
    """Get an agent's run mode."""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.execution_mode if agent and agent.execution_mode else "batch"
    finally:
        db.close()


def get_agent_config(agent_name: str) -> dict:
    """Get an agent's config parameters."""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.config if agent and agent.config else {}
    finally:
        db.close()


async def trigger_agent(agent_name: str) -> str:
    """Run an agent manually (handled by its run mode)."""
    start = time.monotonic()
    trace_id = f"man-{agent_name}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} has no registered implementation")

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent",
        tags={"trigger_source": "manual"},
    ):
        watchlist = load_watchlist_for_agent(agent_name)
        logger.info(
            f"[watchlist] Agent={agent_name} count={len(watchlist)} symbols={[s.symbol for s in watchlist]}"
        )
        if not watchlist:
            return f"Agent {agent_name} has no linked watchlist stocks"

        model, service = resolve_ai_model(agent_name)
        channels = resolve_notify_channels(agent_name)
        _log_trigger_info(agent_name, watchlist, model, service, channels)

        context = build_context(agent_name)
        execution_mode = get_agent_execution_mode(agent_name)
        agent_config = get_agent_config(agent_name)

        # Initialise the agent from its config
        if agent_config:
            agent = agent_cls(**agent_config)
        else:
            agent = agent_cls()

        try:
            if execution_mode == "single" and hasattr(agent, "run_single"):
                # Single mode: analyse stock by stock
                results = []
                for stock in watchlist:
                    result = await agent.run_single(context, stock.symbol)
                    if result:
                        results.append(f"{stock.name}: {result.content[:100]}...")
                msg = "\n\n".join(results) if results else "No unusual moves"
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=msg,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    model_label=context.model_label,
                )
                return msg
            else:
                # Batch mode: analyse all stocks together
                result = await agent.run(context)
                raw = result.raw_data or {}
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=result.content,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    notify_attempted=(
                        "notified" in raw
                        or "notify_error" in raw
                        or "notify_skipped" in raw
                    ),
                    notify_sent=bool(raw.get("notified", False)),
                    model_label=context.model_label,
                )
                return result.content
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise


async def trigger_agent_for_stock(
    agent_name: str,
    stock,
    stock_agent_id: int | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    suppress_notify: bool = False,
    trace_id: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """Run an agent manually (one stock)."""
    start = time.monotonic()
    trace_id = trace_id or f"man-{agent_name}-{stock.symbol}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} has no registered implementation")
    # Entry points that don't go through the stocks.trigger API (such as scheduled runs) need the same lifecycle record;
    # the manual entry point already wrote it, and this idempotent call avoids a duplicate AgentRun.
    try:
        from src.modules.automation.agent_runs import start_agent_run
        start_agent_run(
            agent_name=agent_name,
            trace_id=trace_id,
            trigger_source="manual",
        )
    except Exception as e:
        logger.warning(f"Failed to write AgentRun running state; continuing: {e}")

    settings = Settings()
    proxy = _get_proxy() or settings.http_proxy

    try:
        market = MarketCode(stock.market)
    except ValueError:
        market = MarketCode.IN

    stock_config = StockConfig(
        symbol=stock.symbol,
        name=stock.name,
        market=market,
    )

    # Load the stock's positions
    portfolio = load_portfolio_for_stock(stock.id)

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    channels = [] if suppress_notify else resolve_notify_channels(agent_name, stock_agent_id)
    _log_trigger_info(agent_name, [stock], model, service, channels)

    ai_client = _build_ai_client(model, service, proxy)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=[stock_config])
    context = AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        suppress_notify=suppress_notify,
    )
    # Expose trace_id / force_refresh to the agent (for TradingAgents progress and cache control).
    # AgentContext doesn't declare this field; it is injected with setattr, so other agents are unaffected.
    setattr(context, "_trace_id", trace_id)
    setattr(context, "_force_refresh", force_refresh)

    # Create the agent, with manual trigger parameters. Newer agents such as TradingAgents read config from AgentConfig.
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    elif agent_name == "tradingagents":
        # Read the instantiation parameters from AgentConfig.config
        agent_kwargs = get_agent_config(agent_name) or {}
        try:
            agent = agent_cls(**agent_kwargs)
        except TypeError:
            agent = agent_cls()
    else:
        agent = agent_cls()

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent_for_stock",
        tags={"trigger_source": "manual", "stock_symbol": stock.symbol},
    ):
        try:
            result = await agent.run(context)
            raw = result.raw_data or {}
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=result.content,
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                notify_attempted=(
                    "notified" in raw
                    or "notify_error" in raw
                    or "notify_skipped" in raw
                ),
                notify_sent=bool(raw.get("notified", False)),
                model_label=context.model_label,
            )
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise

    # Return the detailed result
    skipped = bool(result.raw_data.get("skipped", False))
    should_alert = bool(
        result.raw_data.get("should_alert", False if skipped else True)
    )
    return {
        "code": 0 if not skipped else 1001001,
        "success": not skipped,
        "message": result.content if skipped else "ok",
        "title": result.title,
        "content": result.content,
        "should_alert": should_alert,
        "notified": result.raw_data.get("notified", False),
        "skipped": skipped,
    }


@asynccontextmanager
async def lifespan(app):
    """App lifecycle: initialise + start the schedulers."""
    # Compliance first: an invalid ADVISORY_MODE configuration stops startup (ADR-002).
    from src.platform.compliance import get_compliance_settings
    from src.platform.compliance.audit import install_audit_sink

    compliance = get_compliance_settings()
    init_db()
    install_audit_sink()
    setup_logging()
    logger.info("Advisory mode: %s", compliance.mode.value)
    # OTel export (optional, off by default): enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set and
    # the opentelemetry SDK is installed; otherwise a silent no-op that doesn't affect existing deployments.
    try:
        from src.platform.observability.otel import init_otel

        init_otel()
    except Exception as e:  # catch-all: an OTel init error must never block startup
        logger.warning(f"OTel init skipped: {e}")
    setup_proxy()  # set the process env proxy (HTTP_PROXY/NO_PROXY); every httpx client (trust_env=True) follows it
    setup_ssl()

    # Initialise auth from environment variables (for Docker deployments)
    from src.modules.administration.api.auth import init_auth_from_env

    db = SessionLocal()
    try:
        if init_auth_from_env(db):
            logger.info("Auth account initialised from environment variables")
    finally:
        db.close()

    seed_agents()
    seed_strategies()
    seed_sample_stocks()

    # At startup, backfill past TradingAgents decisions into the suggestion pool (stock_suggestions).
    # Early TA runs didn't write to the pool; this one-off backfill at startup makes them visible in the "AI items" panel.
    # Idempotent: existing rows aren't written again; rerunning on every start is cheap (only the last 7 days + dedupe).
    from src.platform.compliance import Feature, is_feature_enabled

    if is_feature_enabled(Feature.SUGGESTION_POOL):
        try:
            from src.modules.automation.tradingagents.operations import backfill_tradingagents_suggestions
            backfill_tradingagents_suggestions(days=7)
        except Exception as e:
            logger.warning(f"TradingAgents item backfill failed; skipping: {e}")


    global scheduler, price_alert_scheduler, paper_trading_scheduler, context_maintenance_scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Agent scheduler started")
    try:
        settings = Settings()
        price_alert_scheduler = PriceAlertScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        price_alert_scheduler.start()
        logger.info("Price alert scheduler started")
    except Exception as e:
        logger.error(f"Price alert scheduler failed to start: {e}")
    if not is_feature_enabled(Feature.AI_PAPER_TRADING):
        logger.info("AI-driven paper trading is disabled in research-only mode")
    else:
        try:
            settings = Settings()
            paper_trading_scheduler = PaperTradingScheduler(
                timezone=settings.app_timezone,
                interval_seconds=60,
            )
            paper_trading_scheduler.start()
            logger.info("Simulation scheduler started")
        except Exception as e:
            logger.error(f"Simulation scheduler failed to start: {e}")
    try:
        settings = Settings()
        context_maintenance_scheduler = ContextMaintenanceScheduler(
            timezone=settings.app_timezone,
            eval_interval_hours=6,
            snapshot_retention_days=180,
            outcome_retention_days=365,
        )
        context_maintenance_scheduler.start()
        logger.info("Context maintenance scheduler started")
    except Exception as e:
        logger.error(f"Context maintenance scheduler failed to start: {e}")
    # MCP call log retention: expired audit records are deleted daily at 04:00
    try:
        register_mcp_log_cleanup(scheduler)
    except Exception as e:
        logger.error(f"Failed to register the MCP log cleanup job: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent scheduler stopped")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("Price alert scheduler stopped")
    if paper_trading_scheduler:
        paper_trading_scheduler.shutdown()
        logger.info("Simulation scheduler stopped")
    if context_maintenance_scheduler:
        context_maintenance_scheduler.shutdown()
        logger.info("Context maintenance scheduler stopped")


# Module-level app instance, for uvicorn reload
from src.bootstrap.application import app  # noqa: E402

app.router.lifespan_context = lifespan

# Static file serving in production
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    from src.platform.security.static_files import resolve_static_file

    # SPA routes: every non-API request returns index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        return FileResponse(resolve_static_file(static_dir, path))

    logger.info(f"Static file serving enabled: {static_dir}")


if __name__ == "__main__":
    print("Candlewise running at http://127.0.0.1:8000")
    print("API docs: http://127.0.0.1:8000/docs")
    # Production (Docker `python server.py`) shouldn't reload: uvicorn's file watcher starts an extra reloader
    # process, wastes resources, and writes under data/ easily trigger restarts. For local hot reload use `make dev-api`
    # (uvicorn --reload), or set DEV_RELOAD=1 explicitly.
    _dev_reload = os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=_dev_reload,
        reload_dirs=["src", "."] if _dev_reload else None,
        reload_excludes=["data/*", "frontend/*", ".claude/*"] if _dev_reload else None,
    )
