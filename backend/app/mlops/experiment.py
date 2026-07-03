"""Experiment tracking for model training runs (app/mlops/retrainer.py).

Mirrors the start/complete/record shape of
app/research/experiment_tracker.py, but logs training runs (hyperparameters,
seed, dataset/feature version, training time) rather than backtest runs.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import MLOpsExperiment
from app.db.session import SessionLocal


def _row_to_dict(row: MLOpsExperiment) -> dict:
    return {
        "experiment_id": row.experiment_id,
        "model_name": row.model_name,
        "algorithm": row.algorithm,
        "model_id": row.model_id,
        "hyperparameters": row.hyperparameters,
        "random_seed": row.random_seed,
        "dataset_version": row.dataset_version,
        "feature_version": row.feature_version,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "training_duration_seconds": row.training_duration_seconds,
        "status": row.status,
        "metrics": row.metrics,
        "notes": row.notes,
    }


class ExperimentTracker:
    def start(
        self,
        *,
        model_name: str,
        algorithm: str | None = None,
        hyperparameters: dict | None = None,
        random_seed: int | None = None,
        dataset_version: str | None = None,
        feature_version: str | None = None,
        db: Session | None = None,
    ) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = MLOpsExperiment(
                experiment_id=uuid.uuid4().hex[:12],
                model_name=model_name,
                algorithm=algorithm,
                hyperparameters=hyperparameters,
                random_seed=random_seed,
                dataset_version=dataset_version,
                feature_version=feature_version,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def complete(
        self,
        experiment_id: str,
        *,
        metrics: dict | None = None,
        model_id: str | None = None,
        status: str = "success",
        notes: str | None = None,
        db: Session | None = None,
    ) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.query(MLOpsExperiment).filter(MLOpsExperiment.experiment_id == experiment_id).first()
            if row is None:
                return None

            finished_at = datetime.now(timezone.utc)
            started_at = row.started_at.replace(tzinfo=timezone.utc) if row.started_at and row.started_at.tzinfo is None else row.started_at
            row.finished_at = finished_at
            row.training_duration_seconds = (finished_at - started_at).total_seconds() if started_at else None
            row.status = status
            row.metrics = metrics
            if model_id:
                row.model_id = model_id
            if notes:
                row.notes = notes

            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def get(self, experiment_id: str, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.query(MLOpsExperiment).filter(MLOpsExperiment.experiment_id == experiment_id).first()
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def list_all(self, model_name: str | None = None, limit: int = 200, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(MLOpsExperiment)
            if model_name:
                query = query.filter(MLOpsExperiment.model_name == model_name)
            rows = query.order_by(MLOpsExperiment.started_at.desc()).limit(limit).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()


tracker = ExperimentTracker()
