"""Recorded additive Trading Horizon schema upgrade.

Controlled deployment command (not run automatically by this module):
    cd backend && python -m app.db.trading_horizon_migration
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.db.models import (
    ExecutionFenceCounter,
    ExecutionIntentAudit,
    ExecutionIntentLock,
    TradingHorizonDecision,
    TradingHorizonConsumption,
    TradingHorizonTimeframeLink,
)
from app.db.session import engine

REVISION = "20260715_01_trading_horizon_authority"
MIGRATION_TABLES = (
    TradingHorizonDecision.__table__,
    TradingHorizonTimeframeLink.__table__,
    TradingHorizonConsumption.__table__,
    ExecutionIntentLock.__table__,
    ExecutionIntentAudit.__table__,
    ExecutionFenceCounter.__table__,
)


def migration_table_names() -> set[str]:
    return {table.name for table in MIGRATION_TABLES}


def upgrade(bind=engine) -> bool:
    with bind.begin() as connection:
        dialect = connection.dialect.name
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
        true_default = "TRUE" if dialect == "postgresql" else "1"
        connection.execute(text(f"CREATE TABLE IF NOT EXISTS schema_migrations (revision VARCHAR PRIMARY KEY, applied_at {timestamp_type} NOT NULL)"))
        recorded = bool(connection.execute(
            text("SELECT 1 FROM schema_migrations WHERE revision=:revision"), {"revision": REVISION}
        ).first())
        changed = False
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "user_bot_settings" in tables:
            existing = {column["name"] for column in inspector.get_columns("user_bot_settings")}
            additions = {
                "trading_profile": "VARCHAR NOT NULL DEFAULT 'auto_adaptive'",
                "strict_timeframe_unanimity": f"BOOLEAN NOT NULL DEFAULT {true_default}",
                "auto_profile_enabled": f"BOOLEAN NOT NULL DEFAULT {true_default}",
                "profile_updated_at": timestamp_type,
                "profile_revision": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, sql_type in additions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE user_bot_settings ADD COLUMN {name} {sql_type}"))
                    changed = True
        if "execution_intent_locks" in tables:
            existing = {column["name"] for column in inspector.get_columns("execution_intent_locks")}
            if "fencing_token" not in existing:
                connection.execute(text("ALTER TABLE execution_intent_locks ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0"))
                changed = True
            if "heartbeat_at" not in existing:
                connection.execute(text(f"ALTER TABLE execution_intent_locks ADD COLUMN heartbeat_at {timestamp_type}"))
                changed = True
        referenced = {"active_drive_decisions"}
        missing_references = referenced - set(inspect(connection).get_table_names())
        if missing_references:
            raise RuntimeError(f"Trading Horizon migration prerequisites missing: {', '.join(sorted(missing_references))}")
        for table in MIGRATION_TABLES:
            if table.name not in tables:
                changed = True
            table.create(connection, checkfirst=True)
        if bind.dialect.name == "sqlite":
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS trading_horizon_decisions_immutable_update BEFORE UPDATE ON trading_horizon_decisions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_HORIZON_DECISION'); END"))
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS trading_horizon_decisions_immutable_delete BEFORE DELETE ON trading_horizon_decisions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_HORIZON_DECISION'); END"))
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS trading_horizon_links_immutable_update BEFORE UPDATE ON trading_horizon_timeframe_links BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_HORIZON_LINK'); END"))
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS trading_horizon_links_immutable_delete BEFORE DELETE ON trading_horizon_timeframe_links BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_HORIZON_LINK'); END"))
        if not recorded:
            connection.execute(text("INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)"),
                               {"revision": REVISION})
            changed = True
    return changed


if __name__ == "__main__":
    upgrade()
