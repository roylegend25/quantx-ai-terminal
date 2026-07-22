"""Recorded, one-time reshape of risk_settings from a singleton row (id=1,
shared by Paper and Binance Real) into a scoped table (one row per scope).

Controlled deployment command (not run automatically by this module, though
init_db() also calls upgrade() so a fresh install and an existing production
DB both converge on the same end state):
    cd backend && python -m app.db.risk_settings_scope_migration

Safety: the existing id=1 row's values become the "paper" scope untouched -
this is exactly what real_risk_gate.py and the paper path both read today,
so nothing changes numerically for existing installs at cutover. A new
"binance_real" row is cloned from the same current values (today they are
already the same row, so this is a no-op in effect, not a silent change to
whatever Binance Real was "really" running). min_point_margin/
min_total_evidence are backfilled from the current active_drive_* env
defaults on both rows for the same reason.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.core.config import settings
from app.db.session import engine

REVISION = "20260722_01_risk_settings_scope"


def upgrade(bind=engine) -> bool:
    with bind.begin() as connection:
        dialect = connection.dialect.name
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
        connection.execute(text(
            f"CREATE TABLE IF NOT EXISTS schema_migrations (revision VARCHAR PRIMARY KEY, applied_at {timestamp_type} NOT NULL)"
        ))
        recorded = bool(connection.execute(
            text("SELECT 1 FROM schema_migrations WHERE revision=:revision"), {"revision": REVISION}
        ).first())

        inspector = inspect(connection)
        if "risk_settings" not in inspector.get_table_names():
            # Fresh install: Base.metadata.create_all already produced the
            # scoped table with no rows. Nothing to reshape.
            if not recorded:
                connection.execute(text("INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)"),
                                   {"revision": REVISION})
                return True
            return False

        existing = {column["name"] for column in inspector.get_columns("risk_settings")}
        changed = False
        additions = {
            "scope": "VARCHAR",
            "version": "INTEGER",
            "min_point_margin": "FLOAT",
            "min_total_evidence": "FLOAT",
        }
        for name, sql_type in additions.items():
            if name not in existing:
                connection.execute(text(f'ALTER TABLE "risk_settings" ADD COLUMN "{name}" {sql_type}'))
                changed = True

        # Backfill: the pre-existing singleton row (id=1, or any row with a
        # NULL scope from an older schema) becomes scope="paper". This is an
        # UPDATE, never an INSERT, so the row's own risk values are
        # preserved exactly - only the new columns are populated.
        row = connection.execute(text('SELECT id FROM "risk_settings" WHERE scope IS NULL OR scope = :paper'),
                                  {"paper": "paper"}).first()
        if row is None:
            row = connection.execute(text('SELECT id FROM "risk_settings" ORDER BY id LIMIT 1')).first()
        if row is not None:
            connection.execute(text(
                'UPDATE "risk_settings" SET scope = :paper, version = COALESCE(version, 1), '
                'min_point_margin = COALESCE(min_point_margin, :point_margin), '
                'min_total_evidence = COALESCE(min_total_evidence, :total_evidence) '
                'WHERE id = :id'
            ), {
                "paper": "paper", "id": row[0],
                "point_margin": settings.active_drive_min_point_margin,
                "total_evidence": settings.active_drive_min_total_evidence,
            })
            changed = True

        # Seed the binance_real row, cloned from the (now-paper) row's
        # current values, unless it already exists.
        has_real = connection.execute(text('SELECT 1 FROM "risk_settings" WHERE scope = :scope'),
                                       {"scope": "binance_real"}).first()
        if not has_real:
            source = connection.execute(text(
                'SELECT min_confidence_to_trade, min_point_margin, min_total_evidence, max_risk_per_trade_pct, '
                'max_daily_loss_pct, max_weekly_loss_pct, max_drawdown_pct, max_consecutive_losses, '
                'max_open_positions, max_position_size_usd, allow_long, allow_short, cooldown_minutes, '
                'paper_trading_enabled, safety_buffer_usdt, safety_buffer_pct '
                'FROM "risk_settings" WHERE scope = :scope'
            ), {"scope": "paper"}).mappings().first()
            if source is not None:
                connection.execute(text(
                    'INSERT INTO "risk_settings" (scope, version, min_confidence_to_trade, min_point_margin, '
                    'min_total_evidence, max_risk_per_trade_pct, max_daily_loss_pct, max_weekly_loss_pct, '
                    'max_drawdown_pct, max_consecutive_losses, max_open_positions, max_position_size_usd, '
                    'allow_long, allow_short, cooldown_minutes, paper_trading_enabled, safety_buffer_usdt, '
                    'safety_buffer_pct, updated_at) VALUES (:scope, 1, :min_confidence_to_trade, :min_point_margin, '
                    ':min_total_evidence, :max_risk_per_trade_pct, :max_daily_loss_pct, :max_weekly_loss_pct, '
                    ':max_drawdown_pct, :max_consecutive_losses, :max_open_positions, :max_position_size_usd, '
                    ':allow_long, :allow_short, :cooldown_minutes, :paper_trading_enabled, :safety_buffer_usdt, '
                    ':safety_buffer_pct, CURRENT_TIMESTAMP)'
                ), {**dict(source), "scope": "binance_real"})
                changed = True

        connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_settings_scope ON "risk_settings" (scope)'))

        if not recorded:
            connection.execute(text("INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)"),
                               {"revision": REVISION})
            changed = True
    return changed


if __name__ == "__main__":
    upgrade()
