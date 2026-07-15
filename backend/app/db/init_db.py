from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db.models import Portfolio, UserBotSetting
from app.strategy.performance_repository import repository as performance_repository
from app.risk import settings_repository as risk_settings_repository
from app.core.config import settings

def _migrate_trade_columns():
    """Add columns introduced after the trades table already existed on disk."""
    inspector = inspect(engine)
    if "trades" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("trades")}
    new_columns = {
        "regime": "VARCHAR",
        "strategy_snapshot": "TEXT",
        "feature_id": "INTEGER",
        # decision provenance - why the bot opened/closed this trade
        "timeframe": "VARCHAR",
        "decision_mode": "VARCHAR",
        "decision_engine_version": "VARCHAR",
        "champion_model_id": "VARCHAR",
        "champion_model_type": "VARCHAR",
        "strategy_used": "VARCHAR",
        "confidence": "FLOAT",
        "required_confidence": "FLOAT",
        "risk_allowed": "BOOLEAN",
        "risk_reason": "TEXT",
        "decision_reasons": "TEXT",
        "model_votes": "TEXT",
        "close_reason": "TEXT",
        # leverage / margin / live-risk editing - nullable, never backfilled
        "user_id": "VARCHAR",
        "leverage": "FLOAT",
        "margin_mode": "VARCHAR",
        "margin_used": "FLOAT",
        "maintenance_margin_rate": "FLOAT",
        "liquidation_price": "FLOAT",
        "trailing_stop": "FLOAT",
        "realized_pnl": "FLOAT",
        "updated_at": "DATETIME",
    }
    with engine.begin() as conn:
        for name, sql_type in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {name} {sql_type}"))

def _migrate_prediction_feature_columns():
    """Add columns introduced after the prediction_features table already
    existed on disk (target/stop, persisted since the AI chart's prediction
    history needs them - older rows keep NULL rather than fabricated values)."""
    inspector = inspect(engine)
    if "prediction_features" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("prediction_features")}
    with engine.begin() as conn:
        if "target" not in existing:
            conn.execute(text("ALTER TABLE prediction_features ADD COLUMN target FLOAT"))
        if "stop" not in existing:
            conn.execute(text("ALTER TABLE prediction_features ADD COLUMN stop FLOAT"))

def _migrate_ml_lab_columns():
    """Columns added by the AI Model Lab (app/ml_lab/) after mlops_models /
    prediction_features already existed on disk. All nullable - older rows
    keep NULL rather than fabricated values."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "mlops_models" in tables:
        existing = {col["name"] for col in inspector.get_columns("mlops_models")}
        new_columns = {
            "model_size_bytes": "INTEGER",
            "training_samples": "INTEGER",
            "test_samples": "INTEGER",
            "dataset_source": "VARCHAR",
            "dataset_spec": "TEXT",
            "precision": "FLOAT",
            "recall": "FLOAT",
            "f1": "FLOAT",
            "roc_auc": "FLOAT",
            "avg_confidence": "FLOAT",
            "avg_prediction_error": "FLOAT",
            "total_trades": "INTEGER",
            "inference_rows_per_sec": "FLOAT",
            "peak_memory_mb": "FLOAT",
            "cpu_info": "VARCHAR",
            "gpu_info": "VARCHAR",
            "oos_accuracy": "FLOAT",
        }
        with engine.begin() as conn:
            for name, sql_type in new_columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE mlops_models ADD COLUMN "{name}" {sql_type}'))

    if "prediction_features" in tables:
        existing = {col["name"] for col in inspector.get_columns("prediction_features")}
        if "latency_ms" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE prediction_features ADD COLUMN latency_ms FLOAT"))


def _migrate_binance_bot_trade_columns():
    """TP/SL protection provenance columns, added after binance_bot_trades
    already existed on disk (Phase 27)."""
    inspector = inspect(engine)
    if "binance_bot_trades" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("binance_bot_trades")}
    new_columns = {
        "decision_engine": "VARCHAR",
        "decision_engine_version": "VARCHAR",
        "entry_order_id": "BIGINT",
        "protection_status": "VARCHAR",
        "protection_failed_reason": "TEXT",
        "protection_confirmed_at": "DATETIME",
        "exit_order_id": "BIGINT",
        "exit_reason": "TEXT",
    }
    with engine.begin() as conn:
        for name, sql_type in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE binance_bot_trades ADD COLUMN {name} {sql_type}"))


def _migrate_protection_provider_columns():
    """Algo Order Provider columns (Phase 26 Algo Order Provider), added
    after exchange_positions / binance_bot_trades already existed on disk."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "exchange_positions" in tables:
        existing = {col["name"] for col in inspector.get_columns("exchange_positions")}
        new_columns = {
            "protection_provider": "VARCHAR",
            "tp_algo_id": "BIGINT",
            "sl_algo_id": "BIGINT",
        }
        with engine.begin() as conn:
            for name, sql_type in new_columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE exchange_positions ADD COLUMN {name} {sql_type}"))

    if "binance_bot_trades" in tables:
        existing = {col["name"] for col in inspector.get_columns("binance_bot_trades")}
        new_columns = {
            "provider_used": "VARCHAR",
            "provider_response": "JSON",
            "provider_latency_ms": "FLOAT",
            "verification_result": "VARCHAR",
            "repair_attempts": "INTEGER DEFAULT 0",
        }
        with engine.begin() as conn:
            for name, sql_type in new_columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE binance_bot_trades ADD COLUMN {name} {sql_type}"))


def _migrate_protection_revision_columns():
    """Confirmed-protection revision/status columns (Phase 30), added after
    exchange_positions already existed on disk - see ExchangePositionRow."""
    inspector = inspect(engine)
    if "exchange_positions" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("exchange_positions")}
    new_columns = {
        "protection_status": "VARCHAR",
        "protection_revision": "INTEGER DEFAULT 0",
        "protection_verified_at": "DATETIME",
    }
    with engine.begin() as conn:
        for name, sql_type in new_columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE exchange_positions ADD COLUMN {name} {sql_type}"))


def _migrate_risk_settings_columns():
    """Live Margin Calculator's advisory safety-reserve columns, added
    after risk_settings already existed on disk."""
    inspector = inspect(engine)
    if "risk_settings" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("risk_settings")}
    with engine.begin() as conn:
        if "safety_buffer_usdt" not in existing:
            conn.execute(text("ALTER TABLE risk_settings ADD COLUMN safety_buffer_usdt FLOAT DEFAULT 1.0"))
        if "safety_buffer_pct" not in existing:
            conn.execute(text("ALTER TABLE risk_settings ADD COLUMN safety_buffer_pct FLOAT DEFAULT 0.10"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_trade_columns()
    _migrate_prediction_feature_columns()
    _migrate_ml_lab_columns()
    _migrate_binance_bot_trade_columns()
    _migrate_protection_provider_columns()
    _migrate_protection_revision_columns()
    _migrate_risk_settings_columns()

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
