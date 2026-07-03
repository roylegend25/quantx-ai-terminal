"""Model evaluation for the ML lifecycle platform.

Scores classification quality with app/ml/evaluator.py::evaluate_binary
(same function every train_*.py script already uses) and trade-level
quality with app/research/metrics.py::compute_metrics (same function every
research module already uses) - this module only assembles the input for
each and persists the combined result as an MLOpsEvaluation row.
"""

import time
from datetime import datetime, timezone
from statistics import mean

from sqlalchemy.orm import Session

from app.db.models import MLOpsEvaluation, PredictionFeature
from app.db.session import SessionLocal
from app.ml.dataset_builder import InsufficientDataError, load_labeled_frame
from app.ml.evaluator import evaluate_binary
from app.research.metrics import compute_metrics


def _closed_trades(symbol: str | None, db: Session) -> tuple[list[dict], list[PredictionFeature]]:
    query = db.query(PredictionFeature).filter(PredictionFeature.outcome.isnot(None))
    if symbol:
        query = query.filter(PredictionFeature.symbol == symbol)
    rows = query.order_by(PredictionFeature.timestamp.asc()).all()

    return [
        {
            "pnl": row.pnl or 0.0,
            "entry_time": row.timestamp,
            "exit_time": row.timestamp,
            "confidence": row.confidence,
            "correct": row.outcome == "WIN",
        }
        for row in rows
    ], rows


def evaluate_model(
    model_id: str | None = None,
    symbol: str | None = None,
    latency_ms: float | None = None,
    db: Session | None = None,
) -> dict:
    """Evaluates against every outcome-labeled prediction currently in the
    feature store (optionally scoped to one symbol). `model_id` is recorded
    on the resulting row for traceability but doesn't change what's scored -
    there's a single shared feature-store dataset, not one per model."""
    owns_session = db is None
    db = db or SessionLocal()
    start = time.perf_counter()
    try:
        trades, rows = _closed_trades(symbol, db)
        trade_metrics = compute_metrics(trades)

        classification = {
            "accuracy": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "auc": None,
        }
        try:
            # load_labeled_frame has no per-symbol column, so classification
            # metrics are always computed across every symbol's outcomes
            frame = load_labeled_frame(db=db)
            if len(frame) > 0:
                y_true = frame["label"]
                y_pred = (frame["direction"] == "LONG").astype(int)
                classification = evaluate_binary(y_true, y_pred)
        except InsufficientDataError:
            pass

        average_r = round(mean(r for r in (row.realized_return for row in rows) if r is not None), 6) if rows else None

        computed_latency_ms = latency_ms if latency_ms is not None else round((time.perf_counter() - start) * 1000, 2)

        row = MLOpsEvaluation(
            model_id=model_id,
            evaluated_at=datetime.now(timezone.utc),
            accuracy=classification.get("accuracy"),
            precision=classification.get("precision"),
            recall=classification.get("recall"),
            f1=classification.get("f1"),
            roc_auc=classification.get("auc"),
            sharpe_ratio=trade_metrics.get("sharpe_ratio"),
            sortino_ratio=trade_metrics.get("sortino_ratio"),
            profit_factor=trade_metrics.get("profit_factor"),
            win_rate=trade_metrics.get("win_rate"),
            average_r=average_r,
            max_drawdown_pct=trade_metrics.get("max_drawdown_pct"),
            expectancy=trade_metrics.get("expectancy"),
            average_confidence=trade_metrics.get("average_confidence"),
            latency_ms=computed_latency_ms,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def _row_to_dict(row: MLOpsEvaluation) -> dict:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
        "accuracy": row.accuracy,
        "precision": row.precision,
        "recall": row.recall,
        "f1": row.f1,
        "roc_auc": row.roc_auc,
        "sharpe_ratio": row.sharpe_ratio,
        "sortino_ratio": row.sortino_ratio,
        "profit_factor": row.profit_factor,
        "win_rate": row.win_rate,
        "average_r": row.average_r,
        "max_drawdown_pct": row.max_drawdown_pct,
        "expectancy": row.expectancy,
        "average_confidence": row.average_confidence,
        "latency_ms": row.latency_ms,
    }


def history(model_id: str | None = None, limit: int = 100, db: Session | None = None) -> list[dict]:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(MLOpsEvaluation)
        if model_id:
            query = query.filter(MLOpsEvaluation.model_id == model_id)
        rows = query.order_by(MLOpsEvaluation.evaluated_at.desc()).limit(limit).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        if owns_session:
            db.close()
