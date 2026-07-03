"""Shared fit -> evaluate -> save -> register pipeline used by every
train_*.py script. Each of those only supplies a model_name and an estimator
factory; everything else (splitting, metrics, persistence, registry
bookkeeping) lives here so the three libraries are trained and compared
identically.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import joblib
from sqlalchemy.orm import Session

from app.ml.dataset_builder import chronological_split, feature_columns, load_labeled_frame
from app.ml.evaluator import evaluate_binary
from app.ml.model_registry import STATUS_CHALLENGER, registry

MODELS_DIR = Path("/app/data/models")


def run_training(
    *,
    model_name: str,
    estimator_factory: Callable[[], object],
    hyperparameters: dict | None = None,
    db: Session | None = None,
) -> dict:
    frame = load_labeled_frame(db=db)
    columns = feature_columns(frame)
    train_df, val_df, test_df = chronological_split(frame)

    X_train, y_train = train_df[columns].fillna(0.0), train_df["label"]
    X_val, y_val = val_df[columns].fillna(0.0), val_df["label"]
    X_test, y_test = test_df[columns].fillna(0.0), test_df["label"]

    model = estimator_factory()
    model.fit(X_train, y_train)

    # validation set is tracked (and recorded on the registry row) even
    # though it isn't used for early stopping here - the held-out test set
    # is what gets reported as the model's headline metrics
    _ = (X_val, y_val)

    y_pred = model.predict(X_test)
    y_score = model.predict_proba(X_test)[:, 1] if hasattr(model, "predict_proba") else None
    metrics = evaluate_binary(y_test, y_pred, y_score)

    feature_importance = None
    if hasattr(model, "feature_importances_"):
        feature_importance = {
            name: round(float(value), 6) for name, value in zip(columns, model.feature_importances_)
        }

    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / f"{model_name}_{version}.joblib"
    joblib.dump({"model": model, "feature_names": columns}, model_path)

    return registry.register(
        model_name=model_name,
        version=version,
        training_samples=len(train_df),
        validation_samples=len(val_df),
        test_samples=len(test_df),
        metrics=metrics,
        feature_importance=feature_importance,
        status=STATUS_CHALLENGER,
        model_path=str(model_path),
        feature_names=columns,
        hyperparameters=hyperparameters,
        db=db,
    )
