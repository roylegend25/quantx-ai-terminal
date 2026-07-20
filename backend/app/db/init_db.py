from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db.models import Portfolio, UserBotSetting
from app.strategy.performance_repository import repository as performance_repository
from app.risk import settings_repository as risk_settings_repository
from app.core.config import settings
from app.db.trading_horizon_migration import migration_table_names, upgrade as upgrade_trading_horizon
from app.db.trading_horizon_issuance_migration import upgrade as upgrade_horizon_issuance


class SchemaCompatibilityError(RuntimeError):
    code = "TRADING_HORIZON_MIGRATION_REQUIRED"

LEGACY_ADDITIVE_COLUMNS = {
    "trades": {
        "regime": "VARCHAR", "strategy_snapshot": "TEXT", "feature_id": "INTEGER",
        "timeframe": "VARCHAR", "decision_mode": "VARCHAR", "decision_engine_version": "VARCHAR",
        "champion_model_id": "VARCHAR", "champion_model_type": "VARCHAR", "strategy_used": "VARCHAR",
        "confidence": "FLOAT", "required_confidence": "FLOAT", "risk_allowed": "BOOLEAN",
        "risk_reason": "TEXT", "decision_reasons": "TEXT", "model_votes": "TEXT", "close_reason": "TEXT",
        "user_id": "VARCHAR", "leverage": "FLOAT", "margin_mode": "VARCHAR", "margin_used": "FLOAT",
        "maintenance_margin_rate": "FLOAT", "liquidation_price": "FLOAT", "trailing_stop": "FLOAT",
        "realized_pnl": "FLOAT", "updated_at": "DATETIME",
        "decision_id": "VARCHAR", "authority_id": "VARCHAR", "execution_mode": "VARCHAR",
        "edge_at_entry": "FLOAT",
    },
    "prediction_features": {"target": "FLOAT", "stop": "FLOAT", "latency_ms": "FLOAT"},
    "mlops_models": {
        "model_size_bytes": "INTEGER", "training_samples": "INTEGER", "test_samples": "INTEGER",
        "dataset_source": "VARCHAR", "dataset_spec": "TEXT", "precision": "FLOAT", "recall": "FLOAT",
        "f1": "FLOAT", "roc_auc": "FLOAT", "avg_confidence": "FLOAT",
        "avg_prediction_error": "FLOAT", "total_trades": "INTEGER", "inference_rows_per_sec": "FLOAT",
        "peak_memory_mb": "FLOAT", "cpu_info": "VARCHAR", "gpu_info": "VARCHAR", "oos_accuracy": "FLOAT",
    },
    "binance_bot_trades": {
        "decision_engine": "VARCHAR", "decision_engine_version": "VARCHAR", "entry_order_id": "BIGINT",
        "protection_status": "VARCHAR", "protection_failed_reason": "TEXT", "protection_confirmed_at": "DATETIME",
        "exit_order_id": "BIGINT", "exit_reason": "TEXT", "provider_used": "VARCHAR",
        "provider_response": "JSON", "provider_latency_ms": "FLOAT", "verification_result": "VARCHAR",
        "repair_attempts": "INTEGER DEFAULT 0",
    },
    "exchange_positions": {
        "protection_provider": "VARCHAR", "tp_algo_id": "BIGINT", "sl_algo_id": "BIGINT",
        "protection_status": "VARCHAR", "protection_revision": "INTEGER DEFAULT 0",
        "protection_verified_at": "DATETIME",
    },
    "risk_settings": {"safety_buffer_usdt": "FLOAT DEFAULT 1.0", "safety_buffer_pct": "FLOAT DEFAULT 0.10"},
    "prediction_ledger": {
        "target_reference_price": "FLOAT", "stop_reference_price": "FLOAT", "data_revision": "VARCHAR", "cycle_id": "VARCHAR",
        # Phase 33: resilient catch-up resolver tracking - additive, older
        # rows keep NULL/0 defaults rather than fabricated history.
        "resolver_attempts": "INTEGER", "last_resolver_attempt_at": "DATETIME",
        "last_resolver_error": "VARCHAR", "next_retry_at": "DATETIME",
        "unresolved_reason": "VARCHAR",
    },
    "prediction_resolutions": {
        "target_hit": "BOOLEAN", "stop_hit": "BOOLEAN", "maximum_favorable_excursion": "FLOAT",
        "maximum_adverse_excursion": "FLOAT",
        "resolution_provider": "VARCHAR", "resolution_exchange": "VARCHAR", "resolution_market_type": "VARCHAR",
        "resolved_market_timestamp": "BIGINT", "resolved_price": "FLOAT", "fallback_used": "BOOLEAN DEFAULT 0",
        "fallback_reason": "VARCHAR", "provider_count_checked": "INTEGER", "provider_price_spread_bps": "FLOAT",
        "resolution_confidence": "FLOAT",
    },
    "active_drive_decisions": {
        "gross_expected_edge": "FLOAT", "net_expected_edge": "FLOAT", "edge_supported": "BOOLEAN",
        "edge_block_reason": "VARCHAR", "edge_sample_size": "INTEGER", "edge_source": "VARCHAR",
    },
}


def _add_missing_columns(bind, table, columns):
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return []
    existing = {column["name"] for column in inspector.get_columns(table)}
    missing = [(name, sql_type) for name, sql_type in columns.items() if name not in existing]
    with bind.begin() as connection:
        for name, sql_type in missing:
            if bind.dialect.name == "postgresql":
                sql_type = sql_type.replace("DATETIME", "TIMESTAMP")
            connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}'))
    return [name for name, _ in missing]


def _migrate_trade_columns(bind=engine):
    """Add columns introduced after the trades table already existed on disk."""
    return _add_missing_columns(bind, "trades", LEGACY_ADDITIVE_COLUMNS["trades"])

def _migrate_prediction_feature_columns(bind=engine):
    """Add columns introduced after the prediction_features table already
    existed on disk (target/stop, persisted since the AI chart's prediction
    history needs them - older rows keep NULL rather than fabricated values)."""
    columns = {key: LEGACY_ADDITIVE_COLUMNS["prediction_features"][key] for key in ("target", "stop")}
    return _add_missing_columns(bind, "prediction_features", columns)

def _migrate_ml_lab_columns(bind=engine):
    """Columns added by the AI Model Lab (app/ml_lab/) after mlops_models /
    prediction_features already existed on disk. All nullable - older rows
    keep NULL rather than fabricated values."""
    changed = _add_missing_columns(bind, "mlops_models", LEGACY_ADDITIVE_COLUMNS["mlops_models"])
    changed += _add_missing_columns(bind, "prediction_features", {"latency_ms": "FLOAT"})
    return changed


def _migrate_binance_bot_trade_columns(bind=engine):
    """TP/SL protection provenance columns, added after binance_bot_trades
    already existed on disk (Phase 27)."""
    columns = {
        "decision_engine": "VARCHAR",
        "decision_engine_version": "VARCHAR",
        "entry_order_id": "BIGINT",
        "protection_status": "VARCHAR",
        "protection_failed_reason": "TEXT",
        "protection_confirmed_at": "DATETIME",
        "exit_order_id": "BIGINT",
        "exit_reason": "TEXT",
    }
    return _add_missing_columns(bind, "binance_bot_trades", columns)


def _migrate_protection_provider_columns(bind=engine):
    """Algo Order Provider columns (Phase 26 Algo Order Provider), added
    after exchange_positions / binance_bot_trades already existed on disk."""
    position_columns = {
            "protection_provider": "VARCHAR",
            "tp_algo_id": "BIGINT",
            "sl_algo_id": "BIGINT",
        }
    trade_columns = {
            "provider_used": "VARCHAR",
            "provider_response": "JSON",
            "provider_latency_ms": "FLOAT",
            "verification_result": "VARCHAR",
            "repair_attempts": "INTEGER DEFAULT 0",
        }
    return (_add_missing_columns(bind, "exchange_positions", position_columns)
            + _add_missing_columns(bind, "binance_bot_trades", trade_columns))


def _migrate_protection_revision_columns(bind=engine):
    """Confirmed-protection revision/status columns (Phase 30), added after
    exchange_positions already existed on disk - see ExchangePositionRow."""
    columns = {
        "protection_status": "VARCHAR",
        "protection_revision": "INTEGER DEFAULT 0",
        "protection_verified_at": "DATETIME",
    }
    return _add_missing_columns(bind, "exchange_positions", columns)


def _migrate_risk_settings_columns(bind=engine):
    """Live Margin Calculator's advisory safety-reserve columns, added
    after risk_settings already existed on disk."""
    return _add_missing_columns(bind, "risk_settings", LEGACY_ADDITIVE_COLUMNS["risk_settings"])

def _migrate_active_drive_ledger_columns(bind=engine):
    """Additive V2 outcome fields. Existing predictions remain immutable
    and retain NULL where the original cycle did not record the value."""
    changed = []
    for table in ("prediction_ledger", "prediction_resolutions"):
        changed += _add_missing_columns(bind, table, LEGACY_ADDITIVE_COLUMNS[table])
    return changed


def _migrate_active_drive_edge_columns(bind=engine):
    """Current-edge audit fields (gross/net edge, sample size, source,
    block reason), added after active_drive_decisions already existed on
    disk. Existing decisions keep NULL - they predate the current-edge
    calculation and never had a real value to backfill."""
    return _add_missing_columns(bind, "active_drive_decisions", LEGACY_ADDITIVE_COLUMNS["active_drive_decisions"])


def _migrate_prediction_ledger_symbol_generated_index(bind=engine):
    """Composite index backing the Prediction Results dashboard's "latest N
    for symbol" query. Without it, SQLite applies the single-column symbol
    index then sorts the entire per-symbol result set to satisfy ORDER BY
    generated_at DESC LIMIT N - measured at ~5s per call against the real
    150k+ row table during rollout verification, entirely fixed by this
    index (SQLite can then walk it back-to-front and stop at N rows)."""
    inspector = inspect(bind)
    if "prediction_ledger" not in inspector.get_table_names():
        return
    with bind.begin() as connection:
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_prediction_ledger_symbol_generated "
            "ON prediction_ledger (symbol, generated_at)"
        ))


def initialize_schema(bind=engine):
    """Explicit administrative schema upgrade; never called implicitly by startup."""
    # Base tables (including active_drive_decisions) must exist before the
    # recorded Horizon migration creates foreign keys to them. Horizon-owned
    # tables are deliberately excluded here so schema ownership is not split.
    horizon_tables = migration_table_names()
    base_tables = [table for table in Base.metadata.sorted_tables if table.name not in horizon_tables]
    Base.metadata.create_all(bind=bind, tables=base_tables)
    # These predate the recorded migration framework.  They remain explicit,
    # independently idempotent stages until they can be converted without
    # inventing revision history for installations where they already ran.
    legacy_stages = (
        _migrate_trade_columns,
        _migrate_prediction_feature_columns,
        _migrate_ml_lab_columns,
        _migrate_binance_bot_trade_columns,
        _migrate_protection_provider_columns,
        _migrate_protection_revision_columns,
        _migrate_risk_settings_columns,
        _migrate_active_drive_ledger_columns,
        _migrate_active_drive_edge_columns,
        _migrate_prediction_ledger_symbol_generated_index,
    )
    for migrate in legacy_stages:
        migrate(bind)
    upgrade_trading_horizon(bind)
    upgrade_horizon_issuance(bind)
    status = check_schema_compatibility(bind)
    if not status["compatible"]:
        raise SchemaCompatibilityError(
            f"SCHEMA_PHYSICAL_STATE_MISMATCH: {status['missing_tables']} {status['missing_columns']}"
        )


def check_schema_compatibility(bind=engine) -> dict:
    """Read-only physical and revision check for normal API/worker startup."""
    from app.db.trading_horizon_migration import REVISION as base_revision
    from app.db.trading_horizon_issuance_migration import REVISION as issuance_revision
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    required_model_tables = set(Base.metadata.tables)
    missing_tables = sorted(required_model_tables - tables)
    required_columns = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.tables.values()
    }
    missing_columns = {
        table: sorted(columns - {column["name"] for column in inspector.get_columns(table)})
        for table, columns in required_columns.items()
        if table in tables
    }
    missing_columns = {table: columns for table, columns in missing_columns.items() if columns}
    revisions = set()
    if "schema_migrations" in tables:
        with bind.connect() as connection:
            revisions = {row[0] for row in connection.execute(text("SELECT revision FROM schema_migrations"))}
    columns = ({column["name"] for column in inspector.get_columns("trading_horizon_decisions")}
               if "trading_horizon_decisions" in tables else set())
    indexes = (inspector.get_indexes("trading_horizon_decisions")
               if "trading_horizon_decisions" in tables else [])
    fingerprint_index = any(index.get("name") == "ix_trading_horizon_decisions_issuance_fingerprint"
                            and index.get("unique") for index in indexes)
    immutable = True
    with bind.connect() as connection:
        if bind.dialect.name == "postgresql" and "trading_horizon_decisions" in tables:
            triggers = {row[0] for row in connection.execute(text(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
            immutable = {"trading_horizon_decisions_immutable_update",
                         "trading_horizon_decisions_immutable_delete",
                         "trading_horizon_links_immutable_update",
                         "trading_horizon_links_immutable_delete"} <= triggers
    required_tables = {"trading_horizon_decisions", "trading_horizon_timeframe_links",
                       "trading_horizon_consumptions", "active_drive_decisions"}
    compatible = (not missing_tables and not missing_columns
                  and base_revision in revisions and issuance_revision in revisions
                  and required_tables <= tables and "issuance_fingerprint" in columns
                  and fingerprint_index and immutable)
    physical_schema_verified = (not missing_tables and not missing_columns
                                and required_tables <= tables
                                and "issuance_fingerprint" in columns
                                and fingerprint_index and immutable)
    return {"compatible": compatible, "code": None if compatible else SchemaCompatibilityError.code,
            "base_revision": base_revision, "base_applied": base_revision in revisions,
            "issuance_revision": issuance_revision, "issuance_applied": issuance_revision in revisions,
            "fingerprint_column": "issuance_fingerprint" in columns,
            "fingerprint_unique_index": fingerprint_index, "immutable": immutable,
            "legacy_additive_stages": {
                table: "pending" if set(columns) & set(missing_columns.get(table, ())) else "applied"
                for table, columns in LEGACY_ADDITIVE_COLUMNS.items()
            },
            "missing_tables": missing_tables, "missing_columns": missing_columns,
            "physical_schema_verified": physical_schema_verified,
            "dialect": bind.dialect.name}


def _migrate_prediction_resolution_provider_columns():
    """Multi-exchange resolution provenance (unresolved-pipeline rebuild).
    Additive only - existing resolved rows keep NULL for these and are still
    valid (they were resolved single-source, before this column existed)."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "prediction_ledger" in tables:
        existing = {col["name"] for col in inspector.get_columns("prediction_ledger")}
        new_columns = {
            "resolver_attempts": "INTEGER DEFAULT 0",
            "last_resolver_attempt_at": "DATETIME",
            "last_resolver_error": "TEXT",
            "unresolved_status": "VARCHAR",
            "resolver_claim_token": "VARCHAR",
            "resolver_claimed_at": "DATETIME",
            "resolver_next_attempt_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, sql_type in new_columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE prediction_ledger ADD COLUMN {name} {sql_type}"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_prediction_ledger_symbol_deadline "
                "ON prediction_ledger (symbol, resolution_deadline)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_prediction_ledger_unresolved_status "
                "ON prediction_ledger (unresolved_status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_prediction_ledger_resolver_due_claim "
                "ON prediction_ledger (symbol, unresolved_status, resolution_deadline, resolver_next_attempt_at)"
            ))

    if "prediction_resolutions" in tables:
        existing = {col["name"] for col in inspector.get_columns("prediction_resolutions")}
        new_columns = {
            "resolution_provider": "VARCHAR",
            "resolution_exchange": "VARCHAR",
            "resolution_market_type": "VARCHAR",
            "provider_symbol": "VARCHAR",
            "requested_due_at": "DATETIME",
            "resolved_market_timestamp": "BIGINT",
            "resolved_price": "FLOAT",
            "fallback_used": "BOOLEAN DEFAULT 0",
            "fallback_reason": "VARCHAR",
            "provider_count_checked": "INTEGER",
            "provider_price_spread_bps": "FLOAT",
            "resolution_confidence": "FLOAT",
        }
        with engine.begin() as conn:
            for name, sql_type in new_columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE prediction_resolutions ADD COLUMN {name} {sql_type}"))


def _migrate_verification_run_columns():
    """Add nullable run associations only. Existing attempt/trade rows stay
    byte-for-byte historical records and intentionally retain NULL run ids."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        if "live_verification_runs" in tables:
            existing = {col["name"] for col in inspector.get_columns("live_verification_runs")}
            additions = {
                "completed_trades_during_this_run": "INTEGER DEFAULT 0 NOT NULL",
                "verification_policy": "JSON",
                "deployment_revision": "VARCHAR",
                "deployment_image_digest": "VARCHAR",
                "initial_exchange_state": "JSON",
                "timestamp_sync_state": "JSON",
            }
            for name, sql_type in additions.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE live_verification_runs ADD COLUMN {name} {sql_type}"))
        for table in ("binance_execution_attempts", "binance_bot_trades"):
            if table not in tables:
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            if "verification_run_id" not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN verification_run_id VARCHAR"))
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_verification_run_id "
                f"ON {table} (verification_run_id)"
            ))


def _migrate_trade_reconciliation_columns():
    """run_counters_applied_at was added after binance_trade_reconciliations
    already existed on disk (see app.trading.pnl_reconciliation) - tracks
    whether a persisted P&L row has actually been applied to its
    LiveVerificationRun counters yet, separately from the row existing at
    all, so a retry can finish that step instead of skipping it forever."""
    inspector = inspect(engine)
    if "binance_trade_reconciliations" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("binance_trade_reconciliations")}
    if "run_counters_applied_at" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE binance_trade_reconciliations ADD COLUMN run_counters_applied_at DATETIME"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_trade_columns()
    _migrate_prediction_feature_columns()
    _migrate_ml_lab_columns()
    _migrate_binance_bot_trade_columns()
    _migrate_protection_provider_columns()
    _migrate_protection_revision_columns()
    _migrate_risk_settings_columns()
    _migrate_active_drive_ledger_columns()
    _migrate_prediction_resolution_provider_columns()
    _migrate_verification_run_columns()
    _migrate_trade_reconciliation_columns()
    inspector = inspect(engine)
    if "trading_control" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("trading_control")}
        with engine.begin() as conn:
            if "execution_enabled" not in existing:
                conn.execute(text("ALTER TABLE trading_control ADD COLUMN execution_enabled BOOLEAN DEFAULT 1 NOT NULL"))
            if "execution_state" not in existing:
                conn.execute(text("ALTER TABLE trading_control ADD COLUMN execution_state VARCHAR DEFAULT 'running' NOT NULL"))
    status = check_schema_compatibility(engine)
    if not status["compatible"]:
        raise SchemaCompatibilityError(SchemaCompatibilityError.code)

    db: Session = SessionLocal()
    try:
        portfolio = db.get(Portfolio, 1)
        if not portfolio:
            portfolio = Portfolio(
                id=1,
                balance=10000.0,
                equity=10000.0,
                daily_pnl=0.0,
                total_pnl=0.0,
                wins=0,
                losses=0,
            )
            db.add(portfolio)
            db.commit()

        performance_repository.seed_defaults(db)
        risk_settings_repository.get_settings(db=db)
        if db.get(UserBotSetting, settings.admin_username) is None:
            db.add(UserBotSetting(user_id=settings.admin_username, decision_engine="active_drive_v2", compare_engines_shadow=False))
            db.commit()
    finally:
        db.close()
