"""PanWatch 统一服务入口 - Web 后台 + Agent 调度"""

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

# 全局 scheduler 实例，供 agents API 调用
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None
paper_trading_scheduler: PaperTradingScheduler | None = None
context_maintenance_scheduler: ContextMaintenanceScheduler | None = None


def apply_proxy_env(proxy: str | None) -> None:
    """统一更新进程环境变量代理,让所有 httpx 默认 Client (trust_env=True) 走该代理。

    传空字符串 / None 时清除环境变量(取消代理)。
    NO_PROXY 默认含 localhost / 回环地址,避免本地访问绕一圈。
    """
    p = (proxy or "").strip()
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        logger.info(f"HTTP/HTTPS 代理已应用: {p}")
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        logger.info("HTTP/HTTPS 代理已清除")


def setup_proxy():
    """启动时把已配置的 HTTP 代理桥接到环境变量。

    优先级:
    1. 已存在的 HTTP_PROXY / HTTPS_PROXY 环境变量(用户显式覆盖,不动)
    2. app_settings.http_proxy(UI 配置)
    3. .env 中的 http_proxy(Settings.http_proxy)
    """
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        logger.info(
            f"沿用现有环境变量代理: HTTP_PROXY={os.environ.get('HTTP_PROXY', '')} "
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
    """设置 SSL 证书环境（企业代理环境）"""
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
    logger.info(f"SSL 证书已加载: {bundle_path}")


def setup_logging():
    """配置日志: 控制台 + 数据库

    分级策略:
    - root logger 始终 DEBUG,所有日志都会传播到 handler
    - 控制台 handler 按 LOG_LEVEL 过滤(默认 INFO),并丢弃 httpx 等三方库的 < WARNING 噪音
    - DB handler 始终 DEBUG 全量收录,UI 日志板永远可以看到包括心跳/httpx 请求在内的完整记录
    """
    console_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    console_level = getattr(logging, console_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    install_log_record_factory()

    # reload/server restart 时避免重复 handler 导致日志放大。
    for h in list(root.handlers):
        if isinstance(h, DBLogHandler) or getattr(h, "_panwatch_console", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # 控制台输出: 按 LOG_LEVEL 过滤,且丢弃三方库的低级别噪音
    console = logging.StreamHandler()
    console._panwatch_console = True  # type: ignore[attr-defined]
    console.setLevel(console_level)
    console.addFilter(_ConsoleNoiseFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # 数据库持久化: 始终全量收录,UI 日志板可查 DEBUG
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)

    # uvicorn 默认给自己挂了 stderr handler 并且 propagate=False,导致 access log
    # 走自己的链路(`INFO: 127.0.0.1 - "GET /api/..."`)不被我们的 filter 拦截。
    # 改成清空自己的 handler + propagate 到 root,让 _ConsoleNoiseFilter 生效。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)


class _ConsoleNoiseFilter(logging.Filter):
    """控制台 handler 过滤器: 三方库的 INFO/DEBUG 不进 stdout,WARNING+ 仍然显示。
    DB handler 不挂这个过滤器,UI 日志板能看到完整请求记录。

    uvicorn.access 是每条请求的 access log(`INFO: 127.0.0.1 - "GET /api/..." 200 OK`),
    属于底层心跳;uvicorn / uvicorn.error 是应用级日志(启动、报错),保留。"""

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
        # 只在没有任何股票时才添加示例
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
    """初始化内置 Agent 配置"""
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
            # 始终同步 execution_mode（确保代码中的定义生效）
            existing.execution_mode = spec.execution_mode or "batch"
            # 同步 display_name 和 description
            existing.display_name = spec.display_name or existing.display_name
            existing.description = spec.description or existing.description
            existing.kind = spec.kind
            existing.visible = bool(spec.visible)
            existing.lifecycle_status = spec.lifecycle_status or "active"
            existing.replaced_by = spec.replaced_by or ""
            existing.display_order = int(spec.display_order or 0)

            # capability 强制不参与调度，避免旧配置继续触发。
            if spec.kind != AGENT_KIND_WORKFLOW:
                existing.enabled = False
                existing.schedule = ""

            # 仅在用户未配置时补齐默认 config
            if spec.config and (not existing.config):
                existing.config = spec.config
            # 对已存在配置做“向前兼容”的字段补齐（不覆盖用户已有值）
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


def seed_strategies():
    """初始化策略目录。"""
    ensure_strategy_catalog()
    logger.info("策略目录初始化完成")


def load_watchlist_for_agent(agent_name: str) -> list[StockConfig]:
    """从数据库加载某个 Agent 关联的自选股"""
    db = SessionLocal()
    try:
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = [sa.stock_id for sa in stock_agents]
        if not stock_ids:
            return []

        # 绑定优先：只要绑定了 Agent，就纳入执行范围
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
    """从数据库加载某个 Agent 关联股票的持仓信息（包括多账户）"""
    from src.platform.persistence.models import Account, Position

    db = SessionLocal()
    try:
        # 获取 Agent 关联的股票 ID
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = set(sa.stock_id for sa in stock_agents)
        if not stock_ids:
            return PortfolioInfo()

        # 获取所有启用的账户
        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            # 获取该账户中属于关联股票的持仓
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
    """从数据库加载单只股票的持仓信息"""
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
    """从 app_settings 获取 http_proxy"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def _get_app_setting(key: str) -> str:
    """从 app_settings 获取配置（不存在返回空字符串）"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == key).first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def resolve_ai_model(
    agent_name: str, stock_agent_id: int | None = None
) -> tuple[AIModel | None, AIService | None]:
    """解析 AI 模型: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)
    返回 (model, service) 元组"""
    db = SessionLocal()
    try:
        model_id = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.ai_model_id:
                model_id = sa.ai_model_id

        # 2. agent 级别默认
        if not model_id:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.ai_model_id:
                model_id = agent.ai_model_id

        # 3. 系统默认
        if not model_id:
            default_model = db.query(AIModel).filter(AIModel.is_default == True).first()
            if default_model:
                model_id = default_model.id

        # 4. 回退：取第一个
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
    """解析通知渠道: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)"""
    db = SessionLocal()
    try:
        channel_ids = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.notify_channel_ids:
                channel_ids = sa.notify_channel_ids

        # 2. agent 级别默认
        if channel_ids is None:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.notify_channel_ids:
                channel_ids = agent.notify_channel_ids

        # 3. 按 id 列表查询或取系统默认
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
    """根据解析后的渠道列表构建 NotifierManager"""
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
    """根据解析后的 model+service 构建带 failover 的 AI 客户端。

    主候选沿用四级路由选定的 model+service;备选由 build_failover_client 从库里
    其余模型按优先级补齐。返回的 FailoverAIClient 与 AIClient 接口兼容,可原地替换。
    """
    return build_failover_client(model, service, proxy)


def build_context(agent_name: str, stock_agent_id: int | None = None) -> AgentContext:
    """为指定 Agent 构建运行上下文"""
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


# Agent 注册表
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    "premarket_outlook": PremarketOutlookAgent,
    "news_digest": NewsDigestAgent,
    "intraday_monitor": IntradayMonitorAgent,
    "tradingagents": TradingAgentsAgent,
}


def build_scheduler() -> AgentScheduler:
    """构建调度器并注册已启用的 Agent"""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # 设置 context 构建函数（每次执行时动态获取最新配置）
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
                logger.warning(f"Agent {cfg.name} 未在 AGENT_REGISTRY 中注册")
                continue
            if not cfg.schedule:
                logger.info(f"Agent {cfg.name} 未设置调度计划，跳过")
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
    logger.info("MCP 日志保留期清理任务已注册")


def reload_scheduler() -> bool:
    """重载调度器（用于配置导入/批量修改后立即生效）"""
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
        logger.info("Agent 调度器已重载")
        return True
    except Exception as e:
        logger.error(f"Agent 调度器重载失败: {e}")
        return False


def _log_trigger_info(
    agent_name: str,
    stocks: list,
    model: AIModel | None,
    service: AIService | None,
    channels: list[NotifyChannel],
):
    """打印 Agent 触发时的上下文信息"""
    stock_names = ", ".join(
        f"{s.name}({s.symbol})" if hasattr(s, "symbol") else str(s) for s in stocks
    )
    ai_info = f"{service.name}/{model.model}" if model and service else "未配置"
    channel_info = ", ".join(ch.name for ch in channels) if channels else "无"
    logger.info(
        f"[触发] Agent={agent_name} | 股票=[{stock_names}] | AI={ai_info} | 通知=[{channel_info}]"
    )


def get_agent_execution_mode(agent_name: str) -> str:
    """获取 Agent 的执行模式"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.execution_mode if agent and agent.execution_mode else "batch"
    finally:
        db.close()


def get_agent_config(agent_name: str) -> dict:
    """获取 Agent 的配置参数"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.config if agent and agent.config else {}
    finally:
        db.close()


async def trigger_agent(agent_name: str) -> str:
    """手动触发 Agent 执行（根据执行模式处理）"""
    start = time.monotonic()
    trace_id = f"man-{agent_name}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未注册实际实现")

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
            return f"Agent {agent_name} 没有关联的自选股"

        model, service = resolve_ai_model(agent_name)
        channels = resolve_notify_channels(agent_name)
        _log_trigger_info(agent_name, watchlist, model, service, channels)

        context = build_context(agent_name)
        execution_mode = get_agent_execution_mode(agent_name)
        agent_config = get_agent_config(agent_name)

        # 根据配置初始化 Agent
        if agent_config:
            agent = agent_cls(**agent_config)
        else:
            agent = agent_cls()

        try:
            if execution_mode == "single" and hasattr(agent, "run_single"):
                # 单只模式：逐只股票分析
                results = []
                for stock in watchlist:
                    result = await agent.run_single(context, stock.symbol)
                    if result:
                        results.append(f"{stock.name}: {result.content[:100]}...")
                msg = "\n\n".join(results) if results else "无异动"
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
                # 批量模式：所有股票一起分析
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
    """手动触发 Agent 执行（单只股票）"""
    start = time.monotonic()
    trace_id = trace_id or f"man-{agent_name}-{stock.symbol}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未注册实际实现")
    # 自动调度等不经过 stocks.trigger API 的入口也要拥有同样的生命周期记录；
    # 手动入口已提前写入，这里幂等调用可避免重复 AgentRun。
    try:
        from src.modules.automation.agent_runs import start_agent_run
        start_agent_run(
            agent_name=agent_name,
            trace_id=trace_id,
            trigger_source="manual",
        )
    except Exception as e:
        logger.warning(f"写 AgentRun running 状态失败,不影响主流程: {e}")

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

    # 加载该股票的持仓信息
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
    # 暴露 trace_id / force_refresh 给 agent(供 TradingAgents 进度反馈 + 缓存控制使用)。
    # AgentContext 不强制声明此字段,通过 setattr 注入,其他 agent 不受影响。
    setattr(context, "_trace_id", trace_id)
    setattr(context, "_force_refresh", force_refresh)

    # 创建 agent，支持手动触发参数。TradingAgents 等新 agent 从 AgentConfig 读 config。
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    elif agent_name == "tradingagents":
        # 从 AgentConfig.config 读取实例化参数
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

    # 返回详细结果
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
    """应用生命周期: 初始化 + 启动调度器"""
    # Compliance first: an invalid ADVISORY_MODE configuration stops startup (ADR-002).
    from src.platform.compliance import get_compliance_settings
    from src.platform.compliance.audit import install_audit_sink

    compliance = get_compliance_settings()
    init_db()
    install_audit_sink()
    setup_logging()
    logger.info("Advisory mode: %s", compliance.mode.value)
    # OTel 导出(可选,默认关闭):仅当配置了 OTEL_EXPORTER_OTLP_ENDPOINT 且装了
    # opentelemetry SDK 时启用,否则静默 no-op,不影响现有部署。
    try:
        from src.platform.observability.otel import init_otel

        init_otel()
    except Exception as e:  # 兜底:OTel 初始化异常绝不阻断服务启动
        logger.warning(f"OTel 初始化跳过: {e}")
    setup_proxy()  # 设置进程 env 代理(HTTP_PROXY/NO_PROXY);所有 httpx(trust_env=True)据此走代理
    setup_ssl()

    # 从环境变量初始化认证（Docker 部署用）
    from src.modules.administration.api.auth import init_auth_from_env

    db = SessionLocal()
    try:
        if init_auth_from_env(db):
            logger.info("已从环境变量初始化认证账号")
    finally:
        db.close()

    seed_agents()
    seed_strategies()
    seed_sample_stocks()

    # 启动时回填历史 TradingAgents 决策到建议池(stock_suggestions)
    # 早期 TA 运行没写建议池,这次启动一次性补齐,让「AI 建议」面板能看到。
    # 幂等:已存在不重复写;每次启动重跑代价极低(只查最近 7 天 + dedupe)。
    from src.platform.compliance import Feature, is_feature_enabled

    if is_feature_enabled(Feature.SUGGESTION_POOL):
        try:
            from src.modules.automation.tradingagents.operations import backfill_tradingagents_suggestions
            backfill_tradingagents_suggestions(days=7)
        except Exception as e:
            logger.warning(f"TradingAgents 建议回填失败,跳过: {e}")


    global scheduler, price_alert_scheduler, paper_trading_scheduler, context_maintenance_scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Agent 调度器已启动")
    try:
        settings = Settings()
        price_alert_scheduler = PriceAlertScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        price_alert_scheduler.start()
        logger.info("价格提醒调度器已启动")
    except Exception as e:
        logger.error(f"价格提醒调度器启动失败: {e}")
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
            logger.info("模拟盘调度器已启动")
        except Exception as e:
            logger.error(f"模拟盘调度器启动失败: {e}")
    try:
        settings = Settings()
        context_maintenance_scheduler = ContextMaintenanceScheduler(
            timezone=settings.app_timezone,
            eval_interval_hours=6,
            snapshot_retention_days=180,
            outcome_retention_days=365,
        )
        context_maintenance_scheduler.start()
        logger.info("上下文维护调度器已启动")
    except Exception as e:
        logger.error(f"上下文维护调度器启动失败: {e}")
    # MCP 调用日志保留期清理:每日 04:00 清理超期审计记录
    try:
        register_mcp_log_cleanup(scheduler)
    except Exception as e:
        logger.error(f"MCP 日志清理任务注册失败: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent 调度器已关闭")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("价格提醒调度器已关闭")
    if paper_trading_scheduler:
        paper_trading_scheduler.shutdown()
        logger.info("模拟盘调度器已关闭")
    if context_maintenance_scheduler:
        context_maintenance_scheduler.shutdown()
        logger.info("上下文维护调度器已关闭")


# 模块级 app 实例，供 uvicorn reload 使用
from src.bootstrap.application import app  # noqa: E402

app.router.lifespan_context = lifespan

# 生产环境静态文件服务
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    from src.platform.security.static_files import resolve_static_file

    # SPA 路由：所有非 API 请求返回 index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        return FileResponse(resolve_static_file(static_dir, path))

    logger.info(f"静态文件服务已启用: {static_dir}")


if __name__ == "__main__":
    print("盯盘侠启动: http://127.0.0.1:8000")
    print("API 文档: http://127.0.0.1:8000/docs")
    # 生产(Docker `python server.py`)不应开 reload:uvicorn 文件监听会多起一个 reloader
    # 子进程、浪费资源,且监听 data/ 写入易误触发重启。本地热重载用 `make dev-api`
    # (uvicorn --reload),或显式设 DEV_RELOAD=1。
    _dev_reload = os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=_dev_reload,
        reload_dirs=["src", "."] if _dev_reload else None,
        reload_excludes=["data/*", "frontend/*", ".claude/*"] if _dev_reload else None,
    )
