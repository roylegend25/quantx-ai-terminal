import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.db.trading_horizon_migration import REVISION, upgrade
from app.db.trading_horizon_issuance_migration import REVISION as ISSUANCE_REVISION, upgrade as upgrade_issuance
from app.db.models import (ActiveDriveDecision, PredictionFeature, Trade,
                           TradingHorizonDecision, TradingHorizonTimeframeLink)
from app.db.init_db import (SchemaCompatibilityError, check_schema_compatibility,
                            initialize_schema, init_db)


def test_recorded_migration_upgrades_current_schema_and_is_rerun_safe(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'upgrade.db'}")
    with engine.begin() as connection:
        ActiveDriveDecision.__table__.create(connection)
        connection.execute(text("CREATE TABLE user_bot_settings (user_id VARCHAR PRIMARY KEY, decision_engine VARCHAR NOT NULL, compare_engines_shadow BOOLEAN NOT NULL)"))
        connection.execute(text("INSERT INTO user_bot_settings VALUES ('existing','active_drive_v2',0)"))
    assert upgrade(engine) is True and upgrade(engine) is False
    inspector=inspect(engine); columns={column["name"] for column in inspector.get_columns("user_bot_settings")}
    assert {"trading_profile","strict_timeframe_unanimity","auto_profile_enabled","profile_revision","profile_updated_at"} <= columns
    assert {"trading_horizon_decisions","trading_horizon_timeframe_links","execution_fence_counters"} <= set(inspector.get_table_names())
    with engine.connect() as connection:
        row=connection.execute(text("SELECT trading_profile, strict_timeframe_unanimity, auto_profile_enabled, profile_revision FROM user_bot_settings WHERE user_id='existing'" )).one()
        assert tuple(row)==("auto_adaptive",1,1,1)
        assert connection.execute(text("SELECT revision FROM schema_migrations")).scalar()==REVISION


def test_recorded_migration_supports_clean_database(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'clean.db'}")
    initialize_schema(engine)
    assert check_schema_compatibility(engine)["compatible"] is True
    tables=inspect(engine).get_table_names()
    assert tables.index("active_drive_decisions") < tables.index("trading_horizon_timeframe_links") or "active_drive_decisions" in tables
    assert "trading_horizon_decisions" in tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),{"revision":REVISION}).scalar()==1
    initialize_schema(engine)


def test_partial_failure_does_not_record_revision(tmp_path, monkeypatch):
    engine=create_engine(f"sqlite:///{tmp_path/'failed.db'}")
    ActiveDriveDecision.__table__.create(engine)
    original=TradingHorizonTimeframeLink.__table__.create
    def fail(*args,**kwargs): raise RuntimeError("injected migration failure")
    monkeypatch.setattr(TradingHorizonTimeframeLink.__table__,"create",fail)
    try:
        try: upgrade(engine)
        except RuntimeError: pass
    finally:
        monkeypatch.setattr(TradingHorizonTimeframeLink.__table__,"create",original)
    with engine.connect() as connection:
        revisions=connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),{"revision":REVISION}).scalar()
    assert revisions==0


def test_sqlite_immutability_triggers_exist(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'triggers.db'}")
    initialize_schema(engine)
    with engine.connect() as connection:
        triggers={row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    assert {"trading_horizon_decisions_immutable_update","trading_horizon_decisions_immutable_delete",
            "trading_horizon_links_immutable_update","trading_horizon_links_immutable_delete"} <= triggers


def test_issuance_fingerprint_upgrade_is_recorded_unique_and_rerun_safe(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'fingerprint.db'}")
    initialize_schema(engine)
    # Simulate an installation created by the first recorded revision.
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations WHERE revision=:revision"),
                           {"revision":ISSUANCE_REVISION})
    assert upgrade_issuance(engine) is True
    assert upgrade_issuance(engine) is False
    inspector=inspect(engine)
    assert "issuance_fingerprint" in {column["name"] for column in inspector.get_columns("trading_horizon_decisions")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),
                                  {"revision":ISSUANCE_REVISION}).scalar()==1


def _database_at_base_revision(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'base-only.db'}")
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO trading_horizon_decisions "
            "(id,issuance_fingerprint,user_id,symbol,selected_profile,profile_revision,"
            "authoritative_execution_timeframe,final_direction,engine_id,engine_version,unanimous,readiness,"
            "execution_eligible,generated_at,expires_at,market_data_revision,performance_snapshot_revision,"
            "expected_edge,directional_confidence,point_margin,evidence,blocking_reasons,created_at) VALUES "
            "('legacy',NULL,'owner','BTCUSDT','mid_term',1,'15m','LONG','active_drive_v2','2.2.0',1,1,1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'m','p',0.1,0.8,5,'{}','[]',CURRENT_TIMESTAMP)"))
        connection.execute(text("DELETE FROM schema_migrations WHERE revision=:revision"),{"revision":ISSUANCE_REVISION})
        connection.execute(text("DROP INDEX ix_trading_horizon_decisions_issuance_fingerprint"))
        connection.execute(text("ALTER TABLE trading_horizon_decisions DROP COLUMN issuance_fingerprint"))
    return engine


def test_explicit_upgrade_from_base_revision_adds_fingerprint_and_preserves_authority(tmp_path):
    engine=_database_at_base_revision(tmp_path)
    before=check_schema_compatibility(engine)
    assert before["base_applied"] and not before["issuance_applied"] and not before["compatible"]
    initialize_schema(engine)
    assert check_schema_compatibility(engine)["compatible"] is True
    with Session(engine) as session:
        authority=session.get(TradingHorizonDecision,"legacy")
        assert authority is not None and authority.final_direction == "LONG"
    with engine.connect() as connection:
        row=connection.execute(text("SELECT id,user_id,final_direction,issuance_fingerprint FROM trading_horizon_decisions")).one()
        assert tuple(row)==("legacy","owner","LONG",None)
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"),
                                  {"revision":ISSUANCE_REVISION}).scalar()==1


def test_issuance_migration_failure_does_not_record_revision_or_report_compatible(tmp_path):
    engine=_database_at_base_revision(tmp_path)

    def fail_index(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith("CREATE UNIQUE INDEX ix_trading_horizon_decisions_issuance_fingerprint"):
            raise RuntimeError("injected index failure")

    event.listen(engine,"before_cursor_execute",fail_index)
    try:
        with pytest.raises(RuntimeError,match="injected index failure"):
            upgrade_issuance(engine)
    finally:
        event.remove(engine,"before_cursor_execute",fail_index)
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE revision=:revision"
        ),{"revision":ISSUANCE_REVISION}).scalar()==0
    assert check_schema_compatibility(engine)["compatible"] is False


def test_normal_init_checks_but_does_not_mutate_base_revision_database(tmp_path,monkeypatch):
    old=_database_at_base_revision(tmp_path)
    monkeypatch.setattr("app.db.init_db.engine",old)
    before={column["name"] for column in inspect(old).get_columns("trading_horizon_decisions")}
    with pytest.raises(SchemaCompatibilityError,match="TRADING_HORIZON_MIGRATION_REQUIRED"):
        init_db()
    after={column["name"] for column in inspect(old).get_columns("trading_horizon_decisions")}
    assert before==after and "issuance_fingerprint" not in after


def test_recorded_horizon_revisions_do_not_hide_missing_legacy_columns(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'revision-physical-mismatch.db'}")
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO trades (id,symbol,side,entry,qty,status) VALUES (7,'BTCUSDT','LONG',10,2,'OPEN')"))
        connection.execute(text("INSERT INTO prediction_features (id,symbol,timeframe,direction,confidence) VALUES (8,'BTCUSDT','15m','LONG',0.8)"))
        connection.execute(text("ALTER TABLE trades DROP COLUMN regime"))
        connection.execute(text("ALTER TABLE prediction_features DROP COLUMN target"))
        connection.execute(text("ALTER TABLE risk_settings DROP COLUMN safety_buffer_usdt"))
    status=check_schema_compatibility(engine)
    assert status["base_applied"] and status["issuance_applied"]
    assert status["compatible"] is False
    assert status["missing_columns"] == {
        "prediction_features": ["target"], "risk_settings": ["safety_buffer_usdt"], "trades": ["regime"]
    }

    initialize_schema(engine)
    assert check_schema_compatibility(engine)["compatible"] is True
    with Session(engine) as session:
        trade=session.get(Trade,7); feature=session.get(PredictionFeature,8)
        assert (trade.symbol,trade.qty,trade.regime)==("BTCUSDT",2,None)
        assert (feature.symbol,feature.confidence,feature.target)==("BTCUSDT",0.8,None)


def test_partial_legacy_stage_is_repaired_idempotently_on_supplied_bind(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'partial.db'}")
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE prediction_features DROP COLUMN stop"))
        connection.execute(text("ALTER TABLE prediction_features DROP COLUMN latency_ms"))
    assert check_schema_compatibility(engine)["legacy_additive_stages"]["prediction_features"] == "pending"
    initialize_schema(engine)
    columns={column["name"] for column in inspect(engine).get_columns("prediction_features")}
    assert {"target","stop","latency_ms"} <= columns
    initialize_schema(engine)
    assert check_schema_compatibility(engine)["compatible"] is True


def test_normal_startup_does_not_repair_missing_legacy_column(tmp_path,monkeypatch):
    old=create_engine(f"sqlite:///{tmp_path/'startup-no-ddl.db'}")
    initialize_schema(old)
    with old.begin() as connection:
        connection.execute(text("ALTER TABLE risk_settings DROP COLUMN safety_buffer_pct"))
    monkeypatch.setattr("app.db.init_db.engine",old)
    with pytest.raises(SchemaCompatibilityError):
        init_db()
    assert "safety_buffer_pct" not in {column["name"] for column in inspect(old).get_columns("risk_settings")}
