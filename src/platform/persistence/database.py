import json
import logging
import os
import shutil
import time
from datetime import datetime

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from src.platform.persistence.legacy_db import DB_FILENAME, adopt_legacy_db
from src.platform.persistence.migrations import (
    has_pending_migrations,
    run_versioned_migrations,
)

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
os.makedirs(_DATA_DIR, exist_ok=True)
adopt_legacy_db(_DATA_DIR)  # data/panwatch.db -> data/candlewise.db, once
DB_PATH = os.path.join(_DATA_DIR, DB_FILENAME)

# SQLite suits local development and single-instance deployments, but concurrent writes can't wait for locks forever.
# Waits are capped at a few seconds so the calling transaction can roll back/retry or return a clear error instead of
# leaving the browser request looking "frozen".
SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_INIT_RETRY_DELAYS = (0.5, 1.0, 2.0)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={
        "timeout": SQLITE_BUSY_TIMEOUT_MS / 1_000,
        "check_same_thread": False,
    },
    poolclass=NullPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialise the database, tolerating brief cross-process locks during dev hot reload."""
    for attempt in range(len(SQLITE_INIT_RETRY_DELAYS) + 1):
        try:
            _init_db_once()
            return
        except Exception as exc:
            if not _is_sqlite_lock_error(exc) or attempt >= len(SQLITE_INIT_RETRY_DELAYS):
                raise
            delay = SQLITE_INIT_RETRY_DELAYS[attempt]
            logger.warning(
                "Database init hit a lock; retrying in %ss (%s/%s): %s",
                delay,
                attempt + 1,
                len(SQLITE_INIT_RETRY_DELAYS),
                exc,
            )
            time.sleep(delay)


def _init_db_once() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate(engine)
    _migrate_old_providers(engine)
    _migrate_settings_to_models(engine)
    _migrate_positions_to_accounts(engine)
    _migrate_remove_stock_enabled(engine)
    if has_pending_migrations(engine):
        _backup_db_before_migration()
    run_versioned_migrations(engine)


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    """Recognise a SQLite lock error wrapped by SQLAlchemy."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if "database is locked" in message or "database table is locked" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _has_column(conn, table: str, column: str) -> bool:
    try:
        conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _has_table(conn, table: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _backfill_sort_order(conn, table: str) -> None:
    """Only take the SQLite write lock when there really is data to backfill."""
    if not _has_column(conn, table, "sort_order"):
        return
    pending = conn.execute(
        text(
            f"SELECT 1 FROM {table} "
            "WHERE sort_order IS NULL OR sort_order = 0 LIMIT 1"
        )
    ).first()
    if not pending:
        return
    conn.execute(
        text(
            f"UPDATE {table} SET sort_order = id "
            "WHERE sort_order IS NULL OR sort_order = 0"
        )
    )
    conn.commit()


def _drop_dangling_ai_provider_fk(conn, table: str) -> None:
    """Drop dangling foreign-key columns that point at the removed ai_providers table.

    Background: _migrate_old_providers dropped the ai_providers table, but the FKs on
    agent_configs.ai_provider_id / stock_agents.ai_provider_id were left behind.
    SQLite doesn't enable PRAGMA foreign_keys by default, so old INSERTs were fine; but some paths
    (e.g. INSERT ... RETURNING + SQLAlchemy validation) fail with "no such table: ai_providers".

    SQLite 3.35+ supports ALTER TABLE DROP COLUMN, so just drop it.
    """
    if not _has_column(conn, table, "ai_provider_id"):
        return
    # Leave it alone while ai_providers still exists (let _migrate_old_providers migrate first)
    if _has_table(conn, "ai_providers"):
        return
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN ai_provider_id"))
        conn.commit()
        logger.info(f"Removed the dangling FK column {table}.ai_provider_id")
    except Exception as e:
        # Old SQLite doesn't support DROP COLUMN: keep the schema and use PRAGMA foreign_keys=OFF instead
        # (per process; harmless, since ai_providers doesn't exist and the FK can never be valid)
        logger.warning(
            f"DROP COLUMN {table}.ai_provider_id failed (SQLite < 3.35?): {e}; "
            f"working around it with PRAGMA foreign_keys=OFF"
        )
        try:
            conn.execute(text("PRAGMA foreign_keys = OFF"))
            conn.commit()
        except Exception:
            pass


def _backup_db_before_migration() -> None:
    """Create a timestamped sqlite backup before versioned migrations."""
    if not os.path.exists(DB_PATH):
        return
    try:
        size = os.path.getsize(DB_PATH)
        if size <= 0:
            return
    except Exception:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.bak.{ts}"
    try:
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"Created a backup before the database migration: {backup_path}")
    except Exception as e:
        logger.warning(f"Backup before the database migration failed: {e}")


def _migrate(engine):
    """Incremental schema migration (SQLite ALTER TABLE ADD COLUMN)."""
    migrations = [
        # Phase 1 (honest simulation): highest price while held, for trailing exits
        (
            "paper_trading_positions",
            "highest_price",
            "ALTER TABLE paper_trading_positions ADD COLUMN highest_price REAL",
        ),
        (
            "stock_agents",
            "schedule",
            "ALTER TABLE stock_agents ADD COLUMN schedule TEXT DEFAULT ''",
        ),
        (
            "agent_configs",
            "ai_model_id",
            "ALTER TABLE agent_configs ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "agent_configs",
            "notify_channel_ids",
            "ALTER TABLE agent_configs ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        (
            "stock_agents",
            "ai_model_id",
            "ALTER TABLE stock_agents ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "stock_agents",
            "notify_channel_ids",
            "ALTER TABLE stock_agents ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        # Phase 3: position enhancements
        (
            "stocks",
            "invested_amount",
            "ALTER TABLE stocks ADD COLUMN invested_amount REAL",
        ),
        # Phase 4: agent run mode
        (
            "agent_configs",
            "execution_mode",
            "ALTER TABLE agent_configs ADD COLUMN execution_mode TEXT DEFAULT 'batch'",
        ),
        # Phase 4: position trading style
        (
            "positions",
            "trading_style",
            "ALTER TABLE positions ADD COLUMN trading_style TEXT DEFAULT 'swing'",
        ),
        # Sort fields: drag-and-drop ordering for the watchlist/holdings
        (
            "stocks",
            "sort_order",
            "ALTER TABLE stocks ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        (
            "positions",
            "sort_order",
            "ALTER TABLE positions ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        # Phase 5: suggestion pool metadata
        (
            "stock_suggestions",
            "meta",
            "ALTER TABLE stock_suggestions ADD COLUMN meta TEXT DEFAULT '{}'",
        ),
    ]
    with engine.connect() as conn:
        for table, column, sql in migrations:
            # Skip tables that no longer exist (e.g. data_sources, dropped by migration 129).
            if _has_table(conn, table) and not _has_column(conn, table, column):
                conn.execute(text(sql))
                conn.commit()

        # Clean up legacy dangling FKs: agent_configs.ai_provider_id / stock_agents.ai_provider_id
        # These columns used to REFERENCE ai_providers(id), but _migrate_old_providers already dropped
        # ai_providers. Keeping the FK makes new INSERTs fail with SQLite "no such table"
        # (SQLite checks on INSERT that the table an FK references exists).
        _drop_dangling_ai_provider_fk(conn, "agent_configs")
        _drop_dangling_ai_provider_fk(conn, "stock_agents")

        # Initialise sort fields (only for rows without them)
        _backfill_sort_order(conn, "stocks")
        _backfill_sort_order(conn, "positions")

        # Create new tables if missing (SQLite)
        if not _has_table(conn, "suggestion_feedback"):
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS suggestion_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  suggestion_id INTEGER NOT NULL REFERENCES stock_suggestions(id) ON DELETE CASCADE,
  useful INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_suggestion_id ON suggestion_feedback(suggestion_id);"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON suggestion_feedback(created_at);"
                )
            )
            conn.commit()


def _migrate_old_providers(engine):
    """If the old ai_providers table exists, migrate its data to ai_services + ai_models."""
    with engine.connect() as conn:
        if not _has_table(conn, "ai_providers"):
            return
        # Check if it has the old schema (has base_url column)
        if not _has_column(conn, "ai_providers", "base_url"):
            return

        rows = conn.execute(
            text(
                "SELECT id, name, base_url, api_key, model, is_default FROM ai_providers"
            )
        ).fetchall()
        if not rows:
            conn.execute(text("DROP TABLE IF EXISTS ai_providers"))
            conn.commit()
            return

        # Group by base_url+api_key to create services
        service_map = {}  # (base_url, api_key) -> service_id
        for row in rows:
            old_id, name, base_url, api_key, model, is_default = row
            key = (base_url, api_key)
            if key not in service_map:
                # Create service
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": name, "base_url": base_url, "api_key": api_key},
                )
                result = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                service_map[key] = result

            service_id = service_map[key]
            conn.execute(
                text(
                    "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, :is_default)"
                ),
                {
                    "name": name,
                    "service_id": service_id,
                    "model": model,
                    "is_default": is_default,
                },
            )
            new_model_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

            # Update references: agent_configs.ai_provider_id → ai_model_id
            if _has_column(conn, "agent_configs", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE agent_configs SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )
            # stock_agents.ai_provider_id → ai_model_id
            if _has_column(conn, "stock_agents", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE stock_agents SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )

        conn.execute(text("DROP TABLE ai_providers"))
        conn.commit()
        logger.info(
            f"Migrated {len(rows)} old AI provider rows to ai_services + ai_models"
        )


def _migrate_settings_to_models(engine):
    """Migrate the old AI/notification settings in app_settings to AIService+AIModel / NotifyChannel rows."""
    with engine.connect() as conn:
        if not _has_table(conn, "app_settings"):
            return

        rows = conn.execute(text("SELECT key, value FROM app_settings")).fetchall()
        settings_map = {row[0]: row[1] for row in rows}

        ai_base_url = settings_map.get("ai_base_url", "")
        ai_api_key = settings_map.get("ai_api_key", "")
        ai_model = settings_map.get("ai_model", "")

        # Migrate AI settings if present and no services exist yet
        if ai_base_url and ai_model:
            existing = conn.execute(text("SELECT COUNT(*) FROM ai_services")).scalar()
            if existing == 0:
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": ai_model, "base_url": ai_base_url, "api_key": ai_api_key},
                )
                service_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                conn.execute(
                    text(
                        "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, 1)"
                    ),
                    {"name": ai_model, "service_id": service_id, "model": ai_model},
                )
                logger.info(f"Migrated AI config: {ai_model}")

        # Migrate Telegram settings if present and no channels exist yet
        bot_token = settings_map.get("notify_telegram_bot_token", "")
        chat_id = settings_map.get("notify_telegram_chat_id", "")

        if bot_token:
            existing = conn.execute(
                text("SELECT COUNT(*) FROM notify_channels")
            ).scalar()
            if existing == 0:
                config_json = json.dumps({"bot_token": bot_token, "chat_id": chat_id})
                conn.execute(
                    text(
                        "INSERT INTO notify_channels (name, type, config, enabled, is_default) VALUES (:name, :type, :config, 1, 1)"
                    ),
                    {"name": "Telegram", "type": "telegram", "config": config_json},
                )
                logger.info("Migrated the Telegram config to a NotifyChannel")

        # Remove old settings keys
        old_keys = [
            "ai_base_url",
            "ai_api_key",
            "ai_model",
            "notify_telegram_bot_token",
            "notify_telegram_chat_id",
        ]
        for key in old_keys:
            if key in settings_map:
                conn.execute(
                    text("DELETE FROM app_settings WHERE key = :key"), {"key": key}
                )

        conn.commit()


def _migrate_positions_to_accounts(engine):
    """
    Migrate position data from the old stocks table to the accounts + positions tables:
    create a default account and move the stocks with positions into it.
    """
    with engine.connect() as conn:
        # Is there account data already? (avoid migrating twice)
        if not _has_table(conn, "accounts"):
            return

        existing_accounts = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        if existing_accounts > 0:
            return

        # Does the stocks table have position data to migrate?
        if not _has_column(conn, "stocks", "cost_price"):
            return

        stocks_with_position = conn.execute(
            text(
                "SELECT id, cost_price, quantity, invested_amount FROM stocks "
                "WHERE cost_price IS NOT NULL AND quantity IS NOT NULL"
            )
        ).fetchall()

        if not stocks_with_position:
            # No position data: create an empty default account
            conn.execute(
                text(
                    "INSERT INTO accounts (name, available_funds, enabled) VALUES ('Default account', 0, 1)"
                )
            )
            conn.commit()
            logger.info("Created the default account")
            return

        # Create the default account
        # Get the old available_funds setting first
        old_funds = conn.execute(
            text("SELECT value FROM app_settings WHERE key = 'available_funds'")
        ).scalar()
        available_funds = float(old_funds) if old_funds else 0

        conn.execute(
            text(
                "INSERT INTO accounts (name, available_funds, enabled) VALUES (:name, :funds, 1)"
            ),
            {"name": "Default account", "funds": available_funds},
        )
        account_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        # Migrate the position data
        for row in stocks_with_position:
            stock_id, cost_price, quantity, invested_amount = row
            conn.execute(
                text(
                    "INSERT INTO positions (account_id, stock_id, cost_price, quantity, invested_amount) "
                    "VALUES (:account_id, :stock_id, :cost_price, :quantity, :invested_amount)"
                ),
                {
                    "account_id": account_id,
                    "stock_id": stock_id,
                    "cost_price": cost_price,
                    "quantity": quantity,
                    "invested_amount": invested_amount,
                },
            )

        # Remove the old available_funds setting
        conn.execute(text("DELETE FROM app_settings WHERE key = 'available_funds'"))

        conn.commit()
        logger.info(f"Migrated {len(stocks_with_position)} positions to the default account")


def _migrate_remove_stock_enabled(engine):
    """Remove the old stocks.enabled soft-delete column and clean up leftover data."""
    with engine.connect() as conn:
        if not _has_table(conn, "stocks") or not _has_column(conn, "stocks", "enabled"):
            return

        # Old soft-deleted rows: delete them if nothing links to them; otherwise restore them as live stocks.
        conn.execute(
            text(
                """
DELETE FROM stocks
WHERE COALESCE(enabled, 1) = 0
  AND id NOT IN (SELECT DISTINCT stock_id FROM positions)
  AND id NOT IN (SELECT DISTINCT stock_id FROM stock_agents)
  AND id NOT IN (SELECT DISTINCT stock_id FROM price_alert_rules)
"""
            )
        )
        conn.execute(text("UPDATE stocks SET enabled = 1 WHERE COALESCE(enabled, 1) = 0"))
        conn.commit()

        # Drop the column directly if possible; old SQLite can't, so rebuild the table to really remove it.
        try:
            conn.execute(text("ALTER TABLE stocks DROP COLUMN enabled"))
            conn.commit()
            logger.info("Removed the stocks.enabled column")
        except Exception:
            conn.rollback()
            logger.info("This SQLite doesn't support DROP COLUMN; rebuilding the stocks table to remove enabled")
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS stocks__new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  market VARCHAR NOT NULL,
  cost_price FLOAT,
  quantity INTEGER,
  invested_amount FLOAT,
  sort_order INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
                )
            )
            conn.execute(
                text(
                    """
INSERT INTO stocks__new (
  id, symbol, name, market, cost_price, quantity, invested_amount, sort_order, created_at, updated_at
)
SELECT
  id, symbol, name, market, cost_price, quantity, invested_amount, COALESCE(sort_order, 0), created_at, updated_at
FROM stocks
"""
                )
            )
            conn.execute(text("DROP TABLE stocks"))
            conn.execute(text("ALTER TABLE stocks__new RENAME TO stocks"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            logger.info("Removed the stocks.enabled column by rebuilding the table")
