import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from src.platform.ai.ai_client import AIClient
from src.platform.compliance import ensure_guarded, guard_title
from src.platform.notifications.notifier import NotifierManager
from src.platform.runtime.config import AppConfig, StockConfig
from src.platform.marketdata.models import MarketCode
from src.platform.notifications.notify_dedupe import build_notify_dedupe_key, check_and_mark_notify
from src.platform.notifications.notify_policy import NotifyPolicy
from src.platform.observability.log_context import log_context

logger = logging.getLogger(__name__)


@dataclass
class PositionInfo:
    """One position."""

    account_id: int
    account_name: str
    stock_id: int
    symbol: str
    name: str
    market: MarketCode
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str = "swing"  # short: short term, swing: swing, long: long term

    @property
    def cost_value(self) -> float:
        """Position cost."""
        return self.cost_price * self.quantity


@dataclass
class AccountInfo:
    """Account."""

    id: int
    name: str
    available_funds: float
    positions: list[PositionInfo] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        """Total position cost of the account."""
        return sum(p.cost_value for p in self.positions)


@dataclass
class PortfolioInfo:
    """Portfolio."""

    accounts: list[AccountInfo] = field(default_factory=list)

    @property
    def total_available_funds(self) -> float:
        """Total available cash."""
        return sum(a.available_funds for a in self.accounts)

    @property
    def total_cost(self) -> float:
        """Total position cost."""
        return sum(a.total_cost for a in self.accounts)

    @property
    def all_positions(self) -> list[PositionInfo]:
        """All positions."""
        result = []
        for acc in self.accounts:
            result.extend(acc.positions)
        return result

    def get_positions_for_stock(self, symbol: str) -> list[PositionInfo]:
        """A stock's positions across accounts."""
        return [p for p in self.all_positions if p.symbol == symbol]

    def get_aggregated_position(self, symbol: str) -> dict | None:
        """
        A stock's combined position (all accounts merged).
        Returns: {"symbol", "name", "total_quantity", "avg_cost", "total_cost", "trading_style", "positions"}
        """
        positions = self.get_positions_for_stock(symbol)
        if not positions:
            return None

        total_quantity = sum(p.quantity for p in positions)
        total_cost = sum(p.cost_value for p in positions)
        avg_cost = total_cost / total_quantity if total_quantity > 0 else 0
        # Trading style of the first position (if accounts use different styles for one stock, short term wins)
        trading_style = positions[0].trading_style
        for p in positions:
            if p.trading_style == "short":
                trading_style = "short"
                break

        return {
            "symbol": symbol,
            "name": positions[0].name,
            "market": positions[0].market,
            "total_quantity": total_quantity,
            "avg_cost": avg_cost,
            "total_cost": total_cost,
            "trading_style": trading_style,
            "positions": positions,
        }

    def has_position(self, symbol: str) -> bool:
        """Whether a stock is held."""
        return any(p.symbol == symbol for p in self.all_positions)


class AgentContext:
    """Agent runtime context."""

    def __init__(
        self,
        ai_client: "AIClient",
        notifier: NotifierManager,
        config: AppConfig,
        portfolio: PortfolioInfo | None = None,
        model_label: str = "",
        notify_policy: NotifyPolicy | None = None,
        suppress_notify: bool = False,
    ):
        self.ai_client = ai_client
        self.notifier = notifier
        self.config = config
        self.portfolio = portfolio if portfolio is not None else PortfolioInfo()
        # Main model label (initial); the model actually used is overwritten by ai_client after failover.
        self._primary_model_label = model_label
        self.notify_policy = notify_policy
        self.suppress_notify = suppress_notify

    @property
    def model_label(self) -> str:
        """Label of the model actually used.

        The failover client records the candidate that succeeded in used_model_label; without it (a plain
        AIClient) this falls back to the main model label chosen by routing. So the footer and the agent_runs
        row both show which model was really used, and routing stays observable.
        """
        used = getattr(self.ai_client, "used_model_label", "")
        return used or self._primary_model_label

    @property
    def watchlist(self) -> list[StockConfig]:
        return self.config.watchlist


@dataclass
class AnalysisResult:
    """Analysis result."""

    agent_name: str
    title: str
    content: str
    # Notification-only content (complete, not truncated); notifications fall back to content when empty.
    # Deep research uses it to send all four analysts' views, while the dialog content stays short.
    notify_content: str | None = None
    raw_data: dict = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def __setattr__(self, name: str, value) -> None:
        # Every user-facing text on a result passes the compliance guard, both at
        # construction and on later reassignment (ADR-002).
        if name == "title" and isinstance(value, str):
            value = guard_title(value, surface="analysis_title")
        elif name in ("content", "notify_content") and isinstance(value, str):
            value = ensure_guarded(value, surface=f"analysis_{name}")
        object.__setattr__(self, name, value)


class BaseAgent(ABC):
    """Abstract agent base class."""

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    async def collect(self, context: AgentContext) -> dict:
        """Collect data."""
        ...

    @abstractmethod
    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        Build the prompt.

        Returns:
            (system_prompt, user_content)
        """
        ...

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """Run the AI analysis."""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        # The title names the stocks
        stock_names = ", ".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" and {len(context.watchlist) - 5} more"
        title = f"[{self.display_name}] {stock_names}"

        # Append the AI model info at the end
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Whether to notify; subclasses may override."""
        return True

    def _notify_dedupe_ttl_minutes(self, context: AgentContext) -> int:
        """Notification idempotency window (minutes).

        P0 policy: per-agent defaults to avoid duplicate notifications.
        """

        if self.name in ("daily_report", "premarket_outlook"):
            default = 12 * 60
        elif self.name == "news_digest":
            default = 60
        # Intraday uses its own per-stock throttle.
        elif self.name == "intraday_monitor":
            default = 30
        elif self.name == "tradingagents":
            # Deep research is costly per run, so the same symbol isn't sent again within 12 hours
            default = 12 * 60
        else:
            default = 60

        policy = getattr(context, "notify_policy", None)
        if policy:
            try:
                return policy.dedupe_ttl_minutes(self.name, default)
            except Exception:
                return default
        return default

    async def run(self, context: AgentContext) -> AnalysisResult:
        """Standard run flow."""
        logger.info(f"Agent [{self.display_name}] started")

        try:
            data = await self.collect(context)
            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                with log_context(
                    event="notify_skipped",
                    notify_status="skipped",
                    notify_reason="suppressed",
                ):
                    logger.info(f"Agent [{self.display_name}] notifications are off for this run")
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            notified = False
            if await self.should_notify(result):
                # Quiet hours: skip sending without marking as error.
                policy = getattr(context, "notify_policy", None)
                if policy:
                    try:
                        if policy.is_quiet_now():
                            with log_context(
                                event="notify_skipped",
                                notify_status="skipped",
                                notify_reason="quiet_hours",
                            ):
                                logger.info(f"Agent [{self.display_name}] inside quiet hours; notification skipped")
                            result.raw_data["notified"] = False
                            result.raw_data["notify_skipped"] = "quiet_hours"
                            return result
                    except Exception:
                        pass

                # Global notification dedupe (idempotency):
                # avoids repeated pushes when an agent is triggered multiple times.
                ttl = self._notify_dedupe_ttl_minutes(context)
                dedupe_key = build_notify_dedupe_key(
                    self.name, result.title, result.notify_content or result.content
                )
                scope = f"__notify__:{dedupe_key}"
                allowed = check_and_mark_notify(
                    agent_name=self.name,
                    scope=scope,
                    ttl_minutes=ttl,
                    mark=False,
                )
                if not allowed:
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason="deduped",
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] duplicate notification; not sending (ttl={ttl}m)"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = "deduped"
                    return result

                with log_context(event="notify_send", notify_status="attempted"):
                    logger.info(f"Agent [{self.display_name}] sending notification")
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.notify_content or result.content,
                    result.images,
                )
                if notify_result.get("skipped"):
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason=str(notify_result.get("skipped") or ""),
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] notification skipped: {notify_result.get('skipped')}"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = notify_result.get("skipped")
                    return result

                notified = bool(notify_result.get("success"))
                if notified:
                    with log_context(
                        event="notify_sent",
                        notify_status="sent",
                    ):
                        logger.info(f"Agent [{self.display_name}] notification sent")
                    # Mark dedupe only after a successful send.
                    check_and_mark_notify(
                        agent_name=self.name,
                        scope=scope,
                        ttl_minutes=ttl,
                        mark=True,
                    )
                else:
                    notify_error = notify_result.get("error") or "Unknown error"
                    with log_context(
                        event="notify_failed",
                        notify_status="failed",
                        notify_reason=str(notify_error),
                    ):
                        logger.error(
                            f"Agent [{self.display_name}] notification failed: {notify_error}"
                        )
                    result.raw_data["notify_error"] = notify_error
            else:
                logger.info(f"Agent [{self.display_name}] no notification needed")

            # Record whether a notification was sent
            result.raw_data["notified"] = notified
            return result

        except Exception as e:
            logger.error(f"Agent [{self.display_name}] failed: {e}")
            raise
