"""Offline training script for the CatBoost challenger.

    python -m app.ml.train_catboost

Registers a Challenger row in the model registry - never touches the live
prediction path. Raises InsufficientDataError (see dataset_builder.py) until
the feature store has enough closed-trade outcomes.
"""

from sqlalchemy.orm import Session

from app.ml.trainer import run_training

MODEL_NAME = "catboost"

HYPERPARAMETERS = {
    "iterations": 200,
    "depth": 4,
    "learning_rate": 0.05,
}


def _estimator_factory():
    from catboost import CatBoostClassifier

    return CatBoostClassifier(verbose=False, allow_writing_files=False, **HYPERPARAMETERS)


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
