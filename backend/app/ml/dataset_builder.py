import json
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import PredictionFeature
from app.db.session import SessionLocal

DATASET_PATH = Path("/app/data/ml_dataset.parquet")

COLUMNS = [
    "id",
    "timestamp",
    "symbol",
    "timeframe",
    "direction",
    "confidence",
    "regime",
    "feature_regime",
    "adaptive_strategy_weights",
    "market_context",
    "multi_timeframe_consensus",
    "technical_features",
    "decision_reason",
    "entry_price",
    "exit_price",
    "pnl",
    "outcome",
    "realized_return",
]

# dict-valued columns get flattened to JSON strings so pyarrow can infer a
# stable type for them even when the dataset has zero or sparse rows
JSON_COLUMNS = [
    "adaptive_strategy_weights",
    "market_context",
    "multi_timeframe_consensus",
    "technical_features",
]


def _row_to_record(row: PredictionFeature) -> dict:
    return {column: getattr(row, column) for column in COLUMNS}


def build_dataset(db: Session | None = None, output_path: Path = DATASET_PATH) -> dict:
    """Flatten every recorded prediction (and, where available, its outcome)
    into a parquet file for future model training. Does not train anything."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(PredictionFeature).order_by(PredictionFeature.id).all()
        records = [_row_to_record(row) for row in rows]
    finally:
        if owns_session:
            db.close()

    df = pd.DataFrame.from_records(records, columns=COLUMNS)

    for column in JSON_COLUMNS:
        df[column] = df[column].apply(lambda v: json.dumps(v) if v is not None else None).astype("string")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)

    return {"path": str(output_path), "rows": len(df)}


# --- ML-ready loading, used by app/ml/trainer.py and app/ml/backtest_models.py ---

MIN_LABELED_SAMPLES = 30

FEATURE_COLUMNS = [
    "ema20",
    "ema50",
    "ema200",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "atr",
    "bb_width",
    "realized_volatility",
    "trend_score",
]

REGIME_CATEGORIES = ["bullish_trend", "bearish_or_weak", "sideways"]

LABEL_COLUMN = "label"


class InsufficientDataError(RuntimeError):
    """Raised when the feature store doesn't yet have enough outcome-backfilled
    rows to train or backtest a model."""


def load_labeled_frame(db: Session | None = None) -> pd.DataFrame:
    """Every prediction_features row that has been backfilled with a real
    trade outcome, flattened into one numeric column per technical feature
    plus a binary WIN/LOSS label."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(PredictionFeature)
            .filter(PredictionFeature.outcome.isnot(None))
            .order_by(PredictionFeature.id)
            .all()
        )
    finally:
        if owns_session:
            db.close()

    records = []
    for row in rows:
        technical_features = row.technical_features or {}
        record = {column: technical_features.get(column) for column in FEATURE_COLUMNS}
        for category in REGIME_CATEGORIES:
            record[f"regime_{category}"] = 1.0 if technical_features.get("regime") == category else 0.0
        record["id"] = row.id
        record["timestamp"] = row.timestamp
        record["direction"] = row.direction
        record["confidence"] = row.confidence
        record[LABEL_COLUMN] = 1 if row.outcome == "WIN" else 0
        records.append(record)

    return pd.DataFrame.from_records(records)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c in FEATURE_COLUMNS or c.startswith("regime_")]


def chronological_split(frame: pd.DataFrame, train_frac: float = 0.7, val_frac: float = 0.15):
    """Splits an already-time-ordered frame into train/validation/test slices.
    Chronological (not random/stratified) so no model is ever validated on
    data from before what it was trained on."""
    if len(frame) < MIN_LABELED_SAMPLES:
        raise InsufficientDataError(
            f"Only {len(frame)} outcome-labeled samples available in the feature "
            f"store; need at least {MIN_LABELED_SAMPLES} to train/backtest."
        )

    n = len(frame)
    train_end = int(n * train_frac)
    val_end = train_end + int(n * val_frac)
    return frame.iloc[:train_end], frame.iloc[train_end:val_end], frame.iloc[val_end:]
