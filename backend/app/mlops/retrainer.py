"""Retrain orchestration for the ML lifecycle platform.

Does not reimplement any training - it calls the same train() entry points
app/ml/train_xgboost.py, train_lightgbm.py and train_catboost.py already
expose (which in turn share app/ml/trainer.py::run_training), or
app/ml/backtest_models.py::backtest_champion for the rule-based adaptive
ensemble. This module only adds the Phase 15 lifecycle bookkeeping around
that: an experiment record, an MLOpsModel row (Testing -> Challenger),
a post-training evaluation pass, and an auto-promotion attempt.
"""

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MLOpsRetrainRun
from app.db.session import SessionLocal
from app.ml.dataset_builder import InsufficientDataError
from app.mlops import evaluation, model_manager
from app.mlops.experiment import tracker as experiment_tracker
from app.mlops.feature_store import DEFAULT_FEATURE_VERSION
from app.mlops.model_registry import STATUS_CHALLENGER, STATUS_FAILED, STATUS_TESTING, registry

ALGORITHMS = ["xgboost", "lightgbm", "catboost"]
CHAMPION_ALGORITHM = "adaptive_ensemble"


def _train_fn(algorithm: str):
    if algorithm == "xgboost":
        from app.ml.train_xgboost import train

        return train
    if algorithm == "lightgbm":
        from app.ml.train_lightgbm import train

        return train
    if algorithm == "catboost":
        from app.ml.train_catboost import train

        return train
    if algorithm == CHAMPION_ALGORITHM:
        from app.ml.backtest_models import backtest_champion

        return backtest_champion
    raise ValueError(f"Unknown algorithm '{algorithm}', expected one of {ALGORITHMS + [CHAMPION_ALGORITHM]}")


def _log_run(triggered_by: str, model_name: str, db: Session) -> MLOpsRetrainRun:
    row = MLOpsRetrainRun(
        triggered_by=triggered_by,
        model_name=model_name,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _finish_run(row: MLOpsRetrainRun, status: str, db: Session, result_model_id: str | None = None, error: str | None = None):
    row.status = status
    row.finished_at = datetime.now(timezone.utc)
    row.result_model_id = result_model_id
    row.error = error
    db.commit()


def retrain(model_name: str, algorithm: str | None = None, reason: str = "manual", db: Session | None = None) -> dict:
    algorithm = algorithm or model_name
    owns_session = db is None
    db = db or SessionLocal()
    try:
        run_row = _log_run(reason, model_name, db)
        experiment = experiment_tracker.start(
            model_name=model_name,
            algorithm=algorithm,
            dataset_version=f"feature-store-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            feature_version=DEFAULT_FEATURE_VERSION,
            db=db,
        )

        train_fn = _train_fn(algorithm)
        start = time.perf_counter()
        try:
            ml_result = train_fn(db=db)
        except InsufficientDataError as exc:
            _finish_run(run_row, "skipped", db, error=str(exc))
            experiment_tracker.complete(experiment["experiment_id"], status="skipped", notes=str(exc), db=db)
            return {"status": "skipped", "reason": str(exc)}
        except Exception as exc:
            _finish_run(run_row, "failed", db, error=repr(exc))
            experiment_tracker.complete(experiment["experiment_id"], status="failed", notes=repr(exc), db=db)
            new_model = registry.register(
                model_name=model_name,
                algorithm=algorithm,
                status=STATUS_FAILED,
                notes=f"retrain reason={reason}: {exc!r}",
                db=db,
            )
            return {"status": "failed", "reason": repr(exc), "model": new_model}

        duration_seconds = round(time.perf_counter() - start, 3)

        new_model = registry.register(
            model_name=model_name,
            algorithm=algorithm,
            status=STATUS_TESTING,
            dataset_version=experiment["dataset_version"],
            feature_version=experiment["feature_version"],
            train_accuracy=ml_result.get("accuracy"),
            training_duration_seconds=duration_seconds,
            parameters=ml_result.get("hyperparameters"),
            feature_list=ml_result.get("feature_names"),
            model_path=ml_result.get("model_path"),
            notes=f"retrain reason={reason}",
            db=db,
        )

        evaluation_result = evaluation.evaluate_model(model_id=new_model["model_id"], db=db)
        registry.update_metrics(
            new_model["model_id"],
            val_accuracy=evaluation_result.get("accuracy"),
            win_rate=evaluation_result.get("win_rate"),
            sharpe_ratio=evaluation_result.get("sharpe_ratio"),
            profit_factor=evaluation_result.get("profit_factor"),
            max_drawdown_pct=evaluation_result.get("max_drawdown_pct"),
            latency_ms=evaluation_result.get("latency_ms"),
            db=db,
        )
        registry.set_status(new_model["model_id"], STATUS_CHALLENGER, db=db)
        new_model = registry.get_by_id(new_model["model_id"], db=db)

        train_metrics = {k: ml_result.get(k) for k in ("accuracy", "precision", "recall", "f1", "auc", "log_loss")}
        experiment_tracker.complete(
            experiment["experiment_id"],
            metrics={**train_metrics, **evaluation_result},
            model_id=new_model["model_id"],
            status="success",
            db=db,
        )
        _finish_run(run_row, "success", db, result_model_id=new_model["model_id"])

        promotion = model_manager.maybe_promote(new_model["model_id"], db=db)

        return {"status": "success", "model": new_model, "evaluation": evaluation_result, "promotion": promotion}
    finally:
        if owns_session:
            db.close()


def should_retrain(db: Session | None = None) -> dict:
    """Checks the two automatic retrain triggers from the spec (accuracy
    below threshold, feature/prediction/market drift above threshold).
    Manual and scheduled triggers are handled by the caller
    (api/models.py and mlops/scheduler.py respectively)."""
    from app.mlops import drift_detector
    from app.mlops.model_registry import STATUS_CHAMPION

    owns_session = db is None
    db = db or SessionLocal()
    try:
        reasons = []

        champions = registry.list_all(status=STATUS_CHAMPION, db=db)
        for champion in champions:
            accuracy = champion.get("val_accuracy") if champion.get("val_accuracy") is not None else champion.get("train_accuracy")
            if accuracy is not None and accuracy < settings.champion_min_accuracy:
                reasons.append({"model_name": champion["model_name"], "trigger": "accuracy", "value": accuracy})

        drift = drift_detector.latest_by_type(db=db)
        for drift_type, record in drift.items():
            if record and record.get("is_drifted"):
                reasons.append({"model_name": None, "trigger": f"drift:{drift_type}", "value": record.get("score")})

        return {"due": len(reasons) > 0, "reasons": reasons}
    finally:
        if owns_session:
            db.close()
