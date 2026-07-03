"""Offline training script for the XGBoost challenger.

    python -m app.ml.train_xgboost

Registers a Challenger row in the model registry - never touches the live
prediction path. Raises InsufficientDataError (see dataset_builder.py) until
the feature store has enough closed-trade outcomes.
"""

from sqlalchemy.orm import Session

from app.ml.trainer import run_training

MODEL_NAME = "xgboost"

HYPERPARAMETERS = {
    "n_estimators": 200,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}


def _estimator_factory():
    from xgboost import XGBClassifier

    return XGBClassifier(
        eval_metric="logloss",
        **HYPERPARAMETERS,
    )


def train(db: Session | None = None) -> dict:
    return run_training(
        model_name=MODEL_NAME,
        estimator_factory=_estimator_factory,
        hyperparameters=HYPERPARAMETERS,
        db=db,
    )


if __name__ == "__main__":
    import json

    print(json.dumps(train(), indent=2, default=str))
