from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.platform.persistence.database import Base


class AIService(Base):
    """AI provider (base_url + api_key)."""

    __tablename__ = "ai_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "OpenAI", "Anthropic", "DeepSeek"
    base_url = Column(String, nullable=False)
    api_key = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    models = relationship(
        "AIModel", back_populates="service", cascade="all, delete-orphan"
    )


class AIModel(Base):
    """AI model (belongs to a provider)."""

    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # display name, e.g. "DeepSeek Chat"
    service_id = Column(
        Integer, ForeignKey("ai_services.id", ondelete="CASCADE"), nullable=False
    )
    model = Column(String, nullable=False)  # the provider's model id, e.g. "deepseek-chat"
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    service = relationship("AIService", back_populates="models")


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "telegram"
    config = Column(JSON, default={})  # {"bot_token": "...", "chat_id": "..."}
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class Account(Base):
    """Brokerage account (for tracking holdings; never trades)."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # account name, e.g. "Zerodha" or "Groww"
    available_funds = Column(Float, default=0)  # available cash
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    positions = relationship(
        "Position", back_populates="account", cascade="all, delete-orphan"
    )


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    name = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN / HK / US
    # Deprecated fields; positions moved to the Position table
    cost_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    invested_amount = Column(Float, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    agents = relationship(
        "StockAgent", back_populates="stock", cascade="all, delete-orphan"
    )
    positions = relationship(
        "Position", back_populates="stock", cascade="all, delete-orphan"
    )


class Position(Base):
    """A position (many accounts, many stocks)."""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_account_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    cost_price = Column(Float, nullable=False)  # cost price
    quantity = Column(Integer, nullable=False)  # quantity
    invested_amount = Column(Float, nullable=True)  # invested amount (used by the intraday monitor)
    sort_order = Column(Integer, default=0)
    trading_style = Column(
        String, default="swing"
    )  # short: short term, swing: swing, long: long term
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="positions")
    stock = relationship("Stock", back_populates="positions")


class StockAgent(Base):
    """Many-to-many: each stock can be watched by several agents."""

    __tablename__ = "stock_agents"
    __table_args__ = (
        UniqueConstraint("stock_id", "agent_name", name="uq_stock_agent"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    agent_name = Column(String, nullable=False)
    schedule = Column(String, default="")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="agents")


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, default="")
    kind = Column(String, default="workflow")  # workflow / capability
    visible = Column(Boolean, default=True)
    lifecycle_status = Column(String, default="active")  # active / deprecated
    replaced_by = Column(String, default="")
    display_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    schedule = Column(String, default="")
    # Run mode: batch (stocks analysed and sent together) / single (one at a time, more timely)
    execution_mode = Column(String, default="batch")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    config = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # running / success / failed
    trace_id = Column(String, default="")
    trigger_source = Column(String, default="")  # schedule / manual / api
    notify_attempted = Column(Boolean, default=False)
    notify_sent = Column(Boolean, default=False)
    context_chars = Column(Integer, default=0)
    model_label = Column(String, default="")
    result = Column(String, default="")
    error = Column(String, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_time_id", "timestamp", "id"),
        Index("ix_log_entries_trace", "trace_id"),
        Index("ix_log_entries_agent_event", "agent_name", "event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    level = Column(String, nullable=False)
    logger_name = Column(String, default="")
    message = Column(String, default="")
    trace_id = Column(String, default="")
    run_id = Column(String, default="")
    agent_name = Column(String, default="")
    event = Column(String, default="")
    tags = Column(JSON, default={})
    notify_status = Column(String, default="")
    notify_reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, default="")
    description = Column(String, default="")


class NewsCache(Base):
    """News cache (for deduplication)."""

    __tablename__ = "news_cache"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_news_source_external"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)  # e.g. "exchange_filing"
    external_id = Column(String, nullable=False)  # the source's id
    title = Column(String, nullable=False)
    content = Column(String, default="")
    publish_time = Column(DateTime, nullable=False)
    symbols = Column(JSON, default=[])  # related symbols
    importance = Column(Integer, default=0)  # importance 0-3
    created_at = Column(DateTime, server_default=func.now())


class NotifyThrottle(Base):
    """Notification throttle record (stops repeat notifications for a stock in a short window)."""

    __tablename__ = "notify_throttle"
    __table_args__ = (
        UniqueConstraint("agent_name", "stock_symbol", name="uq_agent_stock_throttle"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    last_notify_at = Column(DateTime, nullable=False)
    notify_count = Column(Integer, default=1)  # notifications today


class AnalysisHistory(Base):
    """Analysis history (daily close report, pre-market outlook, ...)."""

    __tablename__ = "analysis_history"
    __table_args__ = (
        UniqueConstraint(
            "agent_name", "stock_symbol", "analysis_date", name="uq_agent_stock_date"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)  # "daily_report" / "premarket_outlook"
    stock_symbol = Column(String, nullable=False)  # stock symbol; "*" means market-wide
    analysis_date = Column(String, nullable=False)  # analysis date "YYYY-MM-DD"
    title = Column(String, default="")  # title
    content = Column(String, nullable=False)  # AI analysis text
    raw_data = Column(JSON, default={})  # raw data snapshot
    agent_kind_snapshot = Column(String, default="workflow")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StockContextSnapshot(Base):
    """Structured context snapshot per stock and date (memory across days)."""

    __tablename__ = "stock_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "market",
            "snapshot_date",
            "context_type",
            name="uq_stock_context_snapshot",
        ),
        Index(
            "ix_stock_context_symbol_date",
            "symbol",
            "market",
            "snapshot_date",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    market = Column(String, nullable=False)  # "IN" (India is the only market)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_type = Column(String, nullable=False)  # premarket_outlook/daily_report/...
    payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class NewsTopicSnapshot(Base):
    """News theme snapshot (aggregated per date and window)."""

    __tablename__ = "news_topic_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "window_days",
            name="uq_news_topic_snapshot_date_window",
        ),
        Index("ix_news_topic_snapshot_date", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    window_days = Column(Integer, nullable=False, default=7)
    symbols = Column(JSON, default=[])
    summary = Column(String, default="")
    topics = Column(JSON, default=[])
    sentiment = Column(String, default="neutral")
    coverage = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentContextRun(Base):
    """Summary of the context each agent run used."""

    __tablename__ = "agent_context_runs"
    __table_args__ = (
        Index("ix_agent_context_agent_date", "agent_name", "analysis_date"),
        Index("ix_agent_context_stock_date", "stock_symbol", "analysis_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False, default="*")
    analysis_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentPredictionOutcome(Base):
    """Outcome evaluation of a past item (for replay and statistics)."""

    __tablename__ = "agent_prediction_outcomes"
    __table_args__ = (
        Index(
            "ix_prediction_agent_stock_date",
            "agent_name",
            "stock_symbol",
            "prediction_date",
        ),
        Index("ix_prediction_status_horizon", "outcome_status", "horizon_days"),
        Index("ix_prediction_group_horizon", "prediction_group_id", "horizon_days"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    prediction_date = Column(String, nullable=False)  # YYYY-MM-DD
    horizon_days = Column(Integer, nullable=False, default=1)  # 1/5/10...
    # All horizons of one item share a UUID; empty for old rows, which the query side aggregates.
    prediction_group_id = Column(String, nullable=True)
    # Old rows were evaluated in calendar days; new rows use actual trading days.
    horizon_unit = Column(String, nullable=False, default="trading_days")
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="Watch")
    confidence = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StockSuggestion(Base):
    """Suggestion pool: items from all agents (recommendation mode only)."""

    __tablename__ = "stock_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False, index=True)
    stock_market = Column(String, nullable=False, default="IN", index=True)
    stock_name = Column(String, default="")

    # Content
    action = Column(
        String, nullable=False
    )  # buy/add/reduce/sell/hold/watch/alert/avoid
    action_label = Column(
        String, nullable=False
    )  # label: Open position/Add/Reduce/Exit/Hold/Watch
    signal = Column(String, default="")  # signal
    reason = Column(String, default="")  # reason

    # Source
    agent_name = Column(
        String, nullable=False
    )  # intraday_monitor/daily_report/premarket_outlook
    agent_label = Column(String, default="")  # Intraday monitor / Daily close report / Pre-market outlook

    # Context
    prompt_context = Column(String, default="")  # prompt context summary
    ai_response = Column(String, default="")  # raw AI response

    # Metadata (input snapshot, trigger reason, ...)
    meta = Column(JSON, default={})

    # Times
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)  # expiry

    # Index: fast lookup by market + stock + time
    __table_args__ = (
        Index(
            "ix_suggestion_market_symbol_time",
            "stock_market",
            "stock_symbol",
            "created_at",
        ),
        Index("ix_suggestion_market_expires", "stock_market", "expires_at"),
    )


class EntryCandidate(Base):
    """Screening candidate snapshot (deduped per day, traceable to its source and evidence)."""

    __tablename__ = "entry_candidates"
    __table_args__ = (
        UniqueConstraint(
            "stock_symbol",
            "stock_market",
            "snapshot_date",
            name="uq_entry_candidate_stock_date",
        ),
        Index("ix_entry_candidate_score_date", "snapshot_date", "score"),
        Index("ix_entry_candidate_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    stock_name = Column(String, default="")
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    status = Column(String, default="active")  # active / inactive / invalidated
    score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="Watch")
    signal = Column(String, default="")
    reason = Column(String, default="")
    candidate_source = Column(String, nullable=False, default="watchlist")  # watchlist / market_scan
    strategy_tags = Column(JSON, default=[])
    is_holding_snapshot = Column(Boolean, default=False)
    plan_quality = Column(Integer, default=0)  # 0-100
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_trace_id = Column(String, default="")
    evidence = Column(JSON, default=[])
    plan = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MarketScanSnapshot(Base):
    """Market pool candidate snapshot (for multi-source fallback and coverage diagnostics)."""

    __tablename__ = "market_scan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            name="uq_market_scan_snapshot_symbol",
        ),
        Index("ix_market_scan_snapshot_day_market", "snapshot_date", "stock_market"),
        Index("ix_market_scan_snapshot_source", "snapshot_date", "source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    stock_name = Column(String, default="")
    source = Column(String, nullable=False, default="market_scan")
    score_seed = Column(Float, nullable=False, default=0.0)
    quote = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EntryCandidateFeedback(Base):
    """Feedback on screening candidates (for strategy iteration and quality review)."""

    __tablename__ = "entry_candidate_feedback"
    __table_args__ = (
        Index("ix_entry_feedback_time", "created_at"),
        Index("ix_entry_feedback_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
        Index("ix_entry_feedback_source", "candidate_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    useful = Column(Boolean, default=True)
    reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class EntryCandidateOutcome(Base):
    """Screening candidate outcome (automatic evaluation)."""

    __tablename__ = "entry_candidate_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "horizon_days",
            name="uq_entry_outcome_candidate_horizon",
        ),
        Index("ix_entry_outcome_status_horizon", "outcome_status", "horizon_days"),
        Index("ix_entry_outcome_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("entry_candidates.id", ondelete="CASCADE"), nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StrategyCatalog(Base):
    """Strategy catalogue (versioned, can be switched on/off and re-weighted)."""

    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("code", name="uq_strategy_catalog_code"),
        Index("ix_strategy_catalog_enabled", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    version = Column(String, nullable=False, default="v1")
    enabled = Column(Boolean, default=True)
    market_scope = Column(String, default="ALL")  # ALL or IN
    risk_level = Column(String, default="medium")  # low/medium/high
    params = Column(JSON, default={})
    default_weight = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategySignalRun(Base):
    """Strategy signal snapshot (deduped per day, stock and strategy)."""

    __tablename__ = "strategy_signal_runs"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            "strategy_code",
            "source_candidate_id",
            name="uq_strategy_signal_daily_unique",
        ),
        Index("ix_strategy_signal_snapshot_rank", "snapshot_date", "rank_score"),
        Index("ix_strategy_signal_strategy_market", "strategy_code", "stock_market"),
        Index("ix_strategy_signal_status", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    stock_name = Column(String, default="")

    strategy_code = Column(String, nullable=False)
    strategy_name = Column(String, default="")
    strategy_version = Column(String, default="v1")
    risk_level = Column(String, default="medium")
    source_pool = Column(String, default="watchlist")  # watchlist/market_scan

    score = Column(Float, nullable=False, default=0)
    rank_score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    status = Column(String, default="active")  # active/inactive/invalidated
    action = Column(String, default="watch")
    action_label = Column(String, default="Watch")
    signal = Column(String, default="")
    reason = Column(String, default="")
    evidence = Column(JSON, default=[])
    holding_days = Column(Integer, default=3)

    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    plan_quality = Column(Integer, default=0)

    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_candidate_id = Column(Integer, nullable=True)
    trace_id = Column(String, default="")
    is_holding_snapshot = Column(Boolean, default=False)
    context_quality_score = Column(Float, nullable=True)
    payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyOutcome(Base):
    """Strategy outcome."""

    __tablename__ = "strategy_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "signal_run_id",
            "horizon_days",
            name="uq_strategy_outcome_signal_horizon",
        ),
        Index("ix_strategy_outcome_strategy_horizon", "strategy_code", "horizon_days"),
        Index("ix_strategy_outcome_market_date", "stock_market", "target_date"),
        Index("ix_strategy_outcome_status", "outcome_status", "evaluated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    strategy_code = Column(String, nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    source_pool = Column(String, default="watchlist")
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class BacktestRun(Base):
    """A stored strategy back-test."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_created", "created_at"),
        Index("ix_backtest_runs_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String, nullable=False, default="queued")  # queued/running/succeeded/failed
    strategy_code = Column(String, nullable=False)
    strategy_version = Column(String, default="")
    market = Column(String, nullable=False, default="IN")
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    config = Column(JSON, default={})
    input_snapshot = Column(JSON, default={})
    result = Column(JSON, default={})
    error_message = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class StrategyWeight(Base):
    """Strategy weight (current value)."""

    __tablename__ = "strategy_weights"
    __table_args__ = (
        UniqueConstraint(
            "strategy_code",
            "market",
            "regime",
            name="uq_strategy_weight_key",
        ),
        Index("ix_strategy_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyWeightHistory(Base):
    """Strategy weight history."""

    __tablename__ = "strategy_weight_history"
    __table_args__ = (
        Index("ix_strategy_weight_history_time", "created_at"),
        Index("ix_strategy_weight_history_strategy_market", "strategy_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    window_days = Column(Integer, default=45)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class FactorWeight(Base):
    """Factor weight (current value) per factor and market, calibrated from IC/IR and
    manually overridable.

    Mirrors StrategyWeight at factor level (alpha/catalyst/quality/risk/crowd), so signal
    combination is calibratable instead of an implicit weight of 1.
    """

    __tablename__ = "factor_weights"
    __table_args__ = (
        UniqueConstraint("factor_code", "market", name="uq_factor_weight_key"),
        Index("ix_factor_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="IN")  # "IN" (India is the only market)
    weight = Column(Float, nullable=False, default=1.0)
    is_pinned = Column(Boolean, nullable=False, default=False)  # pinned manually; calibration skips it
    auto_calibrate = Column(Boolean, nullable=False, default=True)  # off: calibration skips it
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FactorWeightHistory(Base):
    """Factor weight history (audit)."""

    __tablename__ = "factor_weight_history"
    __table_args__ = (
        Index("ix_factor_weight_history_time", "created_at"),
        Index("ix_factor_weight_history_factor_market", "factor_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="IN")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    ic = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    reason = Column(String, default="")  # auto/manual/pinned_skip/auto_off/insufficient_samples
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class MarketRegimeSnapshot(Base):
    """Market regime snapshot (for per-market re-weighting and explanations)."""

    __tablename__ = "market_regime_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_market_regime_day_market",
        ),
        Index("ix_market_regime_snapshot", "snapshot_date", "market"),
        Index("ix_market_regime_type", "regime"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="IN")  # "IN" (India is the only market)
    regime = Column(String, nullable=False, default="neutral")  # bullish/neutral/bearish
    regime_score = Column(Float, nullable=False, default=0.0)  # [-1, 1]
    confidence = Column(Float, nullable=False, default=0.0)  # [0, 1]
    breadth_up_pct = Column(Float, nullable=True)
    avg_change_pct = Column(Float, nullable=True)
    volatility_pct = Column(Float, nullable=True)
    active_ratio = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyFactorSnapshot(Base):
    """Factor breakdown of each strategy signal."""

    __tablename__ = "strategy_factor_snapshots"
    __table_args__ = (
        UniqueConstraint("signal_run_id", name="uq_strategy_factor_signal"),
        Index("ix_strategy_factor_snapshot_score", "snapshot_date", "final_score"),
        Index("ix_strategy_factor_strategy_market", "strategy_code", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    strategy_code = Column(String, nullable=False)
    alpha_score = Column(Float, default=0.0)
    catalyst_score = Column(Float, default=0.0)
    quality_score = Column(Float, default=0.0)
    risk_penalty = Column(Float, default=0.0)
    crowd_penalty = Column(Float, default=0.0)
    source_bonus = Column(Float, default=0.0)
    regime_multiplier = Column(Float, default=1.0)
    final_score = Column(Float, nullable=False, default=0.0)
    factor_payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PortfolioRiskSnapshot(Base):
    """Portfolio risk profile per snapshot and market."""

    __tablename__ = "portfolio_risk_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_portfolio_risk_day_market",
        ),
        Index("ix_portfolio_risk_snapshot", "snapshot_date", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="IN")
    total_signals = Column(Integer, default=0)
    active_signals = Column(Integer, default=0)
    held_signals = Column(Integer, default=0)
    unheld_signals = Column(Integer, default=0)
    high_risk_ratio = Column(Float, nullable=True)
    concentration_top5 = Column(Float, nullable=True)
    avg_rank_score = Column(Float, nullable=True)
    risk_level = Column(String, default="medium")  # low/medium/high
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SuggestionFeedback(Base):
    """Feedback on an item (anonymous, lightweight)."""

    __tablename__ = "suggestion_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggestion_id = Column(
        Integer,
        ForeignKey("stock_suggestions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    useful = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class PriceAlertRule(Base):
    """Price alert rule."""

    __tablename__ = "price_alert_rules"
    __table_args__ = (
        Index("ix_price_alert_enabled", "enabled"),
        Index("ix_price_alert_stock_enabled", "stock_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False, default="")
    enabled = Column(Boolean, default=True)
    condition_group = Column(JSON, default={})
    market_hours_mode = Column(String, default="trading_only")  # always/trading_only
    cooldown_minutes = Column(Integer, default=30)
    max_triggers_per_day = Column(Integer, default=3)
    repeat_mode = Column(String, default="repeat")  # once/repeat
    expire_at = Column(DateTime, nullable=True)
    notify_channel_ids = Column(JSON, default=[])
    last_trigger_at = Column(DateTime, nullable=True)
    last_trigger_price = Column(Float, nullable=True)
    trigger_count_today = Column(Integer, default=0)
    trigger_date = Column(String, default="")  # YYYY-MM-DD
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    stock = relationship("Stock")


class PriceAlertHit(Base):
    """Price alert trigger record."""

    __tablename__ = "price_alert_hits"
    __table_args__ = (
        Index("ix_price_alert_hits_rule_time", "rule_id", "trigger_time"),
        UniqueConstraint(
            "rule_id",
            "trigger_bucket",
            name="uq_price_alert_rule_bucket",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(
        Integer, ForeignKey("price_alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    trigger_time = Column(DateTime, server_default=func.now(), nullable=False)
    trigger_bucket = Column(String, nullable=False, default="")  # YYYYMMDDHHMM
    trigger_snapshot = Column(JSON, default={})
    notify_success = Column(Boolean, default=False)
    notify_error = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    rule = relationship("PriceAlertRule")
    stock = relationship("Stock")


class PaperTradingAccount(Base):
    """Simulation account (singleton)."""

    __tablename__ = "paper_trading_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_capital = Column(Float, nullable=False, default=1000000.0)
    current_capital = Column(Float, nullable=False, default=1000000.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    total_trades = Column(Integer, nullable=False, default=0)
    winning_trades = Column(Integer, nullable=False, default=0)
    max_drawdown_pct = Column(Float, nullable=False, default=0.0)
    peak_capital = Column(Float, nullable=False, default=1000000.0)
    enabled = Column(Boolean, default=True)
    excluded_markets = Column(JSON, default=[])  # excluded markets (legacy; derived from market_allocations)
    # Allocation per market, e.g. {"IN": 1.0}; each 0-1, total <= 1 (India is the only market)
    market_allocations = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingPosition(Base):
    """Simulation position."""

    __tablename__ = "paper_trading_positions"
    __table_args__ = (
        Index("ix_paper_pos_status", "status"),
        Index("ix_paper_pos_symbol_market", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)  # highest price while held (for trailing exits)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="open")  # open/closed
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    signal_action = Column(String, default="")
    strategy_code = Column(String, default="")
    opened_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingTrade(Base):
    """Closed simulation trade."""

    __tablename__ = "paper_trading_trades"
    __table_args__ = (
        Index("ix_paper_trade_closed", "closed_at"),
        Index("ix_paper_trade_symbol", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="IN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False, default=0.0)
    pnl_pct = Column(Float, nullable=False, default=0.0)
    exit_reason = Column(String, nullable=False, default="")  # stop_loss/target_price/signal_reversal/manual
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    strategy_code = Column(String, default="")
    holding_days = Column(Integer, default=0)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, server_default=func.now())
    meta = Column(JSON, default={})


class ChatConversation(Base):
    """AI chat session."""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conv_updated", "updated_at"),
        Index("ix_chat_conv_stock", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, default="")
    stock_symbol = Column(String, nullable=True)
    stock_market = Column(String, nullable=True)
    ai_model_id = Column(Integer, nullable=True)
    initial_context = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    """AI chat message."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_msg_conv", "conversation_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    role = Column(String, nullable=False, default="user")  # user/assistant/system
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())


class AssistantContextSnapshot(Base):
    """The latest provider-neutral summary used to build assistant context."""

    __tablename__ = "assistant_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "version", name="ux_assistant_context_snapshot_version"
        ),
        Index(
            "ix_assistant_context_snapshot_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)
    mode = Column(String, nullable=False, default="balanced")
    summary = Column(JSON, nullable=False, default={})
    covered_until_message_id = Column(Integer, nullable=True)
    source_message_count = Column(Integer, nullable=False, default=0)
    usage_before = Column(JSON, nullable=False, default={})
    usage_after = Column(JSON, nullable=False, default={})
    created_at = Column(DateTime, server_default=func.now())


class AssistantTaskRun(Base):
    """Durable execution snapshot for an interactive assistant request."""

    __tablename__ = "assistant_task_runs"
    __table_args__ = (
        Index("ix_assistant_task_run_conversation_created", "conversation_id", "created_at"),
        Index("ix_assistant_task_run_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    user_message_id = Column(Integer, nullable=True)
    final_message_id = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="pending")
    context = Column(JSON, default={})
    checkpoint = Column(JSON, nullable=True)
    state_version = Column(Integer, nullable=False, default=0)
    current_step = Column(Integer, nullable=False, default=0)
    last_event_id = Column(String, nullable=False, default="")
    checkpoint_id = Column(String, nullable=False, default="")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class AssistantTaskEvent(Base):
    """Append-only facts used to replay a durable assistant task."""

    __tablename__ = "assistant_task_events"
    __table_args__ = (
        UniqueConstraint("task_run_id", "sequence", name="ux_assistant_task_event_sequence"),
        UniqueConstraint("event_id", name="ux_assistant_task_event_id"),
        Index("ix_assistant_task_event_run_sequence", "task_run_id", "sequence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    sequence = Column(Integer, nullable=False)
    event_id = Column(String, nullable=False)
    run_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=True)
    step_index = Column(Integer, nullable=True)
    data = Column(JSON, default={})
    occurred_at = Column(DateTime, server_default=func.now())


class AssistantTaskStep(Base):
    """A planned or executed step in an assistant task."""

    __tablename__ = "assistant_task_steps"
    __table_args__ = (Index("ix_assistant_task_step_run_order", "task_run_id", "step_index"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    step_index = Column(Integer, nullable=False)
    title = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="pending")
    detail = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AssistantToolInvocation(Base):
    """One safe tool call, retained after the live event stream expires."""

    __tablename__ = "assistant_tool_invocations"
    __table_args__ = (Index("ix_assistant_tool_invocation_run", "task_run_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    call_id = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    risk = Column(String, nullable=False, default="read")
    arguments = Column(JSON, default={})
    status = Column(String, nullable=False, default="started")
    summary = Column(Text, nullable=False, default="")
    source_data = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


class AssistantToolApproval(Base):
    """One human decision required before an agent tool call can execute."""

    __tablename__ = "assistant_tool_approvals"
    __table_args__ = (
        UniqueConstraint("task_run_id", "call_id", name="ux_assistant_approval_run_call"),
        Index("ix_assistant_approval_run_status", "task_run_id", "status"),
        Index("ix_assistant_approval_pending_expiry", "status", "expires_at"),
    )

    id = Column(String, primary_key=True)
    task_run_id = Column(Integer, nullable=False)
    call_id = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    risk = Column(String, nullable=False)
    arguments = Column(JSON, default={})
    presentation = Column(JSON, default={})
    status = Column(String, nullable=False, default="pending")
    expires_at = Column(DateTime, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AssistantToolPermission(Base):
    """A local principal's durable preference for a tool or risk category."""

    __tablename__ = "assistant_tool_permissions"
    __table_args__ = (
        UniqueConstraint(
            "principal_scope",
            "selector_kind",
            "selector_value",
            name="ux_assistant_permission_selector",
        ),
        Index("ix_assistant_permission_principal", "principal_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    principal_scope = Column(String, nullable=False, default="local")
    selector_kind = Column(String, nullable=False)  # tool | risk
    selector_value = Column(String, nullable=False)
    mode = Column(String, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AssistantArtifact(Base):
    """A durable user-visible output generated by an assistant task."""

    __tablename__ = "assistant_artifacts"
    __table_args__ = (Index("ix_assistant_artifact_run", "task_run_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False, default="")
    content = Column(Text, nullable=False, default="")
    payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class PersonalAccessToken(Base):
    """Personal access token (PAT): a separate long-lived credential for the MCP endpoint.

    Separate from the login JWT, which is a single-user session (30 days, not revocable, no
    scope) and unsuitable for external MCP clients; a PAT can be revoked and audited on its
    own and is read-only. Only sha256(token_hash) is stored; the plaintext is returned once
    at creation. Single-user app, so no user_id.
    """

    __tablename__ = "personal_access_tokens"
    __table_args__ = (
        Index("ix_pat_revoked", "revoked_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, default="")  # user-readable purpose
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    prefix = Column(String(32), nullable=False, default="")  # plaintext prefix, shown in lists
    scopes_json = Column(Text, nullable=False, default="[]")  # JSON: ["mcp:read"]
    expires_at = Column(DateTime, nullable=True)  # None = never expires
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String, nullable=True)
    revoked_at = Column(DateTime, nullable=True)  # set = revoked
    created_at = Column(DateTime, server_default=func.now())


class MCPCallLog(Base):
    """Audit record of each MCP tool call (metadata only; no argument or result text)."""

    __tablename__ = "mcp_call_logs"
    __table_args__ = (
        Index("ix_mcp_log_tool", "tool_name"),
        Index("ix_mcp_log_called", "called_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pat_id = Column(Integer, nullable=True)  # soft reference; history survives PAT deletion
    pat_prefix = Column(String, nullable=True)
    tool_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="ok")  # ok / error
    error_message = Column(Text, nullable=True)
    args_summary = Column(Text, nullable=True)  # redacted, truncated summary
    duration_ms = Column(Integer, default=0)
    client_ip = Column(String, nullable=True)
    called_at = Column(DateTime, server_default=func.now())


class ComplianceEvent(Base):
    """A guard decision that was not ``passed`` (redacted or blocked). Admin-only data.

    ``original_text`` is kept for tuning the guard and is purged after the retention
    period (see ``src/platform/compliance/audit.py``).
    """

    __tablename__ = "compliance_events"
    __table_args__ = (
        Index("ix_compliance_event_created", "created_at"),
        Index("ix_compliance_event_surface", "surface"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    surface = Column(String(64), nullable=False, default="")
    status = Column(String(16), nullable=False)  # redacted / blocked
    rule_ids = Column(JSON, default=list)
    original_sha256 = Column(String(64), nullable=False, default="")
    original_text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())


class RAReviewItem(Base):
    """AI recommendation draft awaiting review by a SEBI-registered research analyst.

    Only written in ``ra_registered`` mode. Nothing is published until a reviewer approves
    it (review UI arrives in Phase 5).
    """

    __tablename__ = "ra_review_items"
    __table_args__ = (Index("ix_ra_review_status", "status"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    surface = Column(String(64), nullable=False, default="")
    subject = Column(String, nullable=False, default="")
    original_ai_output = Column(Text, nullable=False, default="")
    edited_output = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="pending")  # pending/approved/rejected
    reviewer = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    ra_registration_number = Column(String(32), nullable=False, default="")
    disclosure_text = Column(Text, nullable=False, default="")
    published_at = Column(DateTime, nullable=True)
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())


class BrokerConnection(Base):
    """One user's connection to one market data broker (India fork, Phase 2).

    ``credentials_enc`` (API key/secret, client code, PIN) and ``session_enc`` (access
    token) are CredentialVault tokens bound to ``user_id|id|field``; they are never
    returned by the API. ``key_hint`` is a masked hint for the UI. ``session_expires_at``
    is stored as naive UTC like the rest of this schema. ``user_id`` is "local" until
    multi-user auth arrives in Phase 5. Typed with ``Mapped`` so mypy --strict can check
    the code that uses it.
    """

    __tablename__ = "broker_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_broker_connection_user_provider"),
        Index("ix_broker_connection_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    credentials_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disconnected")
    last_error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
