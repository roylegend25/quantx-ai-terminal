"""Opt-in PostgreSQL integration coverage using an explicitly supplied test DB."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.init_db import check_schema_compatibility, initialize_schema
from app.db.trading_horizon_issuance_migration import REVISION as ISSUANCE_REVISION
from app.db.models import ActiveDriveDecision
from app.db.trading_horizon_migration import REVISION, upgrade


POSTGRES_URL=os.getenv("TEST_POSTGRES_URL")
pytestmark=pytest.mark.skipif(not POSTGRES_URL,reason="TEST_POSTGRES_URL temporary PostgreSQL database is not configured")


@pytest.fixture
def pg_engine():
    schema=f"horizon_test_{uuid.uuid4().hex}"
    admin=create_engine(POSTGRES_URL)
    with admin.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(POSTGRES_URL,connect_args={"options":f"-csearch_path={schema}"})
    try: yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection: connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_fresh_postgres_initialization_and_rerun(pg_engine):
    initialize_schema(pg_engine); initialize_schema(pg_engine)
    inspector=inspect(pg_engine); tables=set(inspector.get_table_names())
    assert {"active_drive_decisions","trading_horizon_decisions","trading_horizon_timeframe_links"} <= tables
    foreign_keys=inspector.get_foreign_keys("trading_horizon_timeframe_links")
    assert {fk["referred_table"] for fk in foreign_keys}=={"active_drive_decisions","trading_horizon_decisions"}
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),{"revision":REVISION}).scalar()==1
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),{"revision":ISSUANCE_REVISION}).scalar()==1
    assert check_schema_compatibility(pg_engine)["compatible"] is True


def test_existing_postgres_schema_upgrade_preserves_data(pg_engine):
    with pg_engine.begin() as connection:
        ActiveDriveDecision.__table__.create(connection)
        connection.execute(text("CREATE TABLE user_bot_settings (user_id VARCHAR PRIMARY KEY, decision_engine VARCHAR NOT NULL, compare_engines_shadow BOOLEAN NOT NULL)"))
        connection.execute(text("INSERT INTO user_bot_settings VALUES ('existing','active_drive_v2',FALSE)"))
    assert upgrade(pg_engine) is True and upgrade(pg_engine) is False
    with pg_engine.connect() as connection:
        row=connection.execute(text("SELECT user_id,trading_profile,strict_timeframe_unanimity,profile_revision FROM user_bot_settings")).one()
    assert tuple(row)==("existing","auto_adaptive",True,1)


def test_postgres_base_revision_then_issuance_upgrade(pg_engine):
    initialize_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations WHERE revision=:revision"),
                           {"revision":ISSUANCE_REVISION})
        connection.execute(text("DROP INDEX ix_trading_horizon_decisions_issuance_fingerprint"))
        connection.execute(text("ALTER TABLE trading_horizon_decisions DROP COLUMN issuance_fingerprint"))
    assert check_schema_compatibility(pg_engine)["compatible"] is False
    initialize_schema(pg_engine)
    status=check_schema_compatibility(pg_engine)
    assert status["compatible"] is True and status["immutable"] is True
    columns={column["name"] for column in inspect(pg_engine).get_columns("trading_horizon_decisions")}
    assert "issuance_fingerprint" in columns


def test_postgres_recorded_revisions_do_not_hide_partial_legacy_schema(pg_engine):
    initialize_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("ALTER TABLE trades DROP COLUMN regime"))
        connection.execute(text("ALTER TABLE prediction_features DROP COLUMN target"))
        connection.execute(text("ALTER TABLE risk_settings DROP COLUMN safety_buffer_usdt"))
    status=check_schema_compatibility(pg_engine)
    assert status["base_applied"] and status["issuance_applied"]
    assert status["compatible"] is False
    assert set(status["missing_columns"]) == {"trades","prediction_features","risk_settings"}
    initialize_schema(pg_engine)
    assert check_schema_compatibility(pg_engine)["compatible"] is True
    inspector=inspect(pg_engine)
    assert "regime" in {column["name"] for column in inspector.get_columns("trades")}
    assert "target" in {column["name"] for column in inspector.get_columns("prediction_features")}
    assert "safety_buffer_usdt" in {column["name"] for column in inspector.get_columns("risk_settings")}
