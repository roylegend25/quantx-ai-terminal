from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db.models import Portfolio
from app.strategy.performance_repository import repository as performance_repository
from app.risk import settings_repository as risk_settings_repository

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


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_trade_columns()
    _migrate_prediction_feature_columns()
    _migrate_ml_lab_columns()

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
    finally:
        db.close()
