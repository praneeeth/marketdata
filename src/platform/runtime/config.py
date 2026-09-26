"""Technical boundary for reading runtime settings from the environment and project config files.

Used by HTTP, background tasks and platform adapters alike; it contains no investment or product decisions.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings

from src.platform.marketdata.models import MarketCode


class Settings(BaseSettings):
    """Environment variable settings."""

    # AI
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"

    # Assistant context engineering. The compression model is optional: when
    # unset, the host reuses the configured default assistant model.
    context_compression_model_id: int | None = Field(default=None, ge=1)
    context_compression_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    context_summary_max_tokens: int = Field(default=800, ge=128, le=4_000)
    context_max_tokens: int = Field(default=12_000, ge=256)
    context_soft_limit_tokens: int = Field(default=8_400, ge=128)
    context_hard_limit_tokens: int = Field(default=10_200, ge=256)
    context_keep_recent_messages: int = Field(default=8, ge=1, le=100)
    tool_research_enabled: bool = True

    # Telegram
    notify_telegram_bot_token: str = ""
    notify_telegram_chat_id: str = ""

    # Proxy
    http_proxy: str = ""

    # Notification policy (can be overridden in the UI's System settings)
    # Quiet hours (local time zone), format HH:MM-HH:MM, empty = off; overnight example: 23:00-07:00
    notify_quiet_hours: str = ""
    # Notification retry attempts (not counting the first)
    notify_retry_attempts: int = 2
    # Retry backoff in seconds (base); grows 1x, 2x, ...
    notify_retry_backoff_seconds: float = 2.0
    # Dedupe window overrides (JSON), e.g. {"news_digest":60,"daily_report":720}
    notify_dedupe_ttl_overrides: str = ""

    # SSL certificates (corporate environments)
    ca_cert_file: str = ""

    # Scheduling
    # day_of_week follows POSIX cron (1-5 = Monday to Friday)
    daily_report_cron: str = "30 15 * * 1-5"

    # Default time zone (for scheduling, time display, etc.).
    # One variable controls it: TZ (IANA name). Defaults to India time, since NSE/BSE is
    # the only market and agent schedules such as "15:30" mean IST.
    app_timezone: str = Field(
        default="Asia/Kolkata",
        validation_alias=AliasChoices("TZ", "APP_TIMEZONE"),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # .env may contain undeclared fields such as HTTPS_PROXY (standard httpx/system variables); ignore them
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_context_thresholds(self) -> "Settings":
        if not self.context_soft_limit_tokens < self.context_hard_limit_tokens <= self.context_max_tokens:
            raise ValueError(
                "context thresholds must satisfy soft_limit < hard_limit <= max_tokens"
            )
        return self


@dataclass
class StockConfig:
    """Watchlist config."""

    symbol: str
    name: str
    market: MarketCode


@dataclass
class AppConfig:
    """Full app config."""

    settings: Settings
    watchlist: list[StockConfig] = field(default_factory=list)


def load_watchlist(path: str | Path = "config/watchlist.yaml") -> list[StockConfig]:
    """Load the watchlist from YAML."""
    path = Path(path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    stocks = []
    for market_group in data.get("markets", []):
        market_code = MarketCode(market_group["code"])
        for stock in market_group.get("stocks", []):
            stocks.append(
                StockConfig(
                    symbol=stock["symbol"],
                    name=stock["name"],
                    market=market_code,
                )
            )

    return stocks


def load_config() -> AppConfig:
    """Load the full config."""
    settings = Settings()
    watchlist = load_watchlist()
    return AppConfig(settings=settings, watchlist=watchlist)
