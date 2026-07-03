"""Offline batch job: backtests the live adaptive ensemble against every
closed-trade outcome recorded so far, and (re)trains each ML challenger
against the same feature store. All results are written to the model
registry as the source of truth for GET /api/ml/models and
GET /api/ml/performance.

    python -m app.ml.backtest_models

This never touches the live prediction path - it only reads historical
predictions/outcomes and writes registry rows.
"""

from sqlalchemy.orm import Session

from app.ml.dataset_builder import InsufficientDataError, load_labeled_frame
from app.ml.evaluator import evaluate_binary
from app.ml.model_registry import STATUS_CHAMPION, registry
from app.strategy.performance_repository import repository as performance_repository

CHAMPION_MODEL_NAME = "adaptive_ensemble"


def _probability_up(direction: str, confidence: float | None) -> float:
    confidence = max(0.0, min(100.0, confidence or 0.0))
    if direction == "LONG":
        return (50 + confidence / 2) / 100
    if direction == "SHORT":
        return (50 - confidence / 2) / 100
    return 0.5


def backtest_champion(db: Session | None = None) -> dict:
    """Scores the adaptive ensemble's own historical LONG/SHORT calls against
    the real outcomes those trades produced."""
    frame = load_labeled_frame(db=db)
    frame = frame[frame["direction"].isin(["LONG", "SHORT"])]

    if len(frame) == 0:
        return registry.register(
            model_name=CHAMPION_MODEL_NAME,
            version="live",
            status=STATUS_CHAMPION,
            feature_importance=performance_repository.get_weights(db=db),
            notes="No closed-trade outcomes recorded yet - adaptive ensemble is still the live production model.",
            db=db,
        )

    y_true = frame["label"]
    y_pred = (frame["direction"] == "LONG").astype(int)
    y_score = [
        _probability_up(direction, confidence)
        for direction, confidence in zip(frame["direction"], frame["confidence"])
    ]

    metrics = evaluate_binary(y_true, y_pred, y_score)
    weights = performance_repository.get_weights(db=db)

    return registry.register(
        model_name=CHAMPION_MODEL_NAME,
        version="live",
        test_samples=len(frame),
        metrics=metrics,
        feature_importance=weights,
        status=STATUS_CHAMPION,
        notes="Backtested against every closed-trade outcome recorded so far.",
        db=db,
    )


def backtest_challengers(db: Session | None = None) -> dict:
    from app.ml import train_catboost, train_lightgbm, train_xgboost

    results = {}
    for name, module in (
        ("xgboost", train_xgboost),
        ("lightgbm", train_lightgbm),
        ("catboost", train_catboost),
    ):
        try:
            results[name] = module.train(db=db)
        except InsufficientDataError as exc:
            results[name] = {"status": "skipped", "reason": str(exc)}
    return results


def run_all(db: Session | None = None) -> dict:
    return {
        "champion": backtest_champion(db=db),
        "challengers": backtest_challengers(db=db),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_all(), indent=2, default=str))
