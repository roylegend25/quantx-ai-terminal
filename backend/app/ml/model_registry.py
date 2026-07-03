"""Champion/challenger registry for offline-trained ML models.

The Champion entry always represents the live adaptive ensemble
(app/strategy/ensemble.py) - it is written by app/ml/backtest_models.py from
real closed-trade outcomes, never by a train_*.py script. XGBoost, LightGBM
and CatBoost only ever register as Challengers. Nothing here is read by the
live prediction path (app/api/prediction.py).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import MLModelRegistry
from app.db.session import SessionLocal

STATUS_CHAMPION = "Champion"
STATUS_CHALLENGER = "Challenger"
STATUS_ARCHIVED = "Archived"


def _row_to_dict(row: MLModelRegistry) -> dict:
    return {
        "model_name": row.model_name,
        "version": row.version,
        "trained_at": row.trained_at.isoformat() if row.trained_at else None,
        "training_samples": row.training_samples,
        "validation_samples": row.validation_samples,
        "test_samples": row.test_samples,
        "accuracy": row.accuracy,
        "precision": row.precision,
        "recall": row.recall,
        "f1": row.f1,
        "auc": row.auc,
        "log_loss": row.log_loss,
        "calibration": row.calibration,
        "feature_importance": row.feature_importance,
        "status": row.status,
        "model_path": row.model_path,
        "feature_names": row.feature_names,
        "hyperparameters": row.hyperparameters,
        "notes": row.notes,
    }


class ModelRegistry:
    def register(
        self,
        *,
        model_name: str,
        version: str,
        training_samples: int = 0,
        validation_samples: int = 0,
        test_samples: int = 0,
        metrics: dict | None = None,
        feature_importance: dict | None = None,
        status: str = STATUS_CHALLENGER,
        model_path: str | None = None,
        feature_names: list[str] | None = None,
        hyperparameters: dict | None = None,
        notes: str | None = None,
        db: Session | None = None,
    ) -> dict:
        metrics = metrics or {}
        owns_session = db is None
        db = db or SessionLocal()
        try:
            # keep at most one active (non-archived) row per model_name/status
            # pair - a fresh training run supersedes whatever it previously
            # registered as
            (
                db.query(MLModelRegistry)
                .filter(
                    MLModelRegistry.model_name == model_name,
                    MLModelRegistry.status == status,
                )
                .update({"status": STATUS_ARCHIVED})
            )

            row = MLModelRegistry(
                model_name=model_name,
                version=version,
                trained_at=datetime.now(timezone.utc),
                training_samples=training_samples,
                validation_samples=validation_samples,
                test_samples=test_samples,
                accuracy=metrics.get("accuracy"),
                precision=metrics.get("precision"),
                recall=metrics.get("recall"),
                f1=metrics.get("f1"),
                auc=metrics.get("auc"),
                log_loss=metrics.get("log_loss"),
                calibration=metrics.get("calibration"),
                feature_importance=feature_importance,
                status=status,
                model_path=model_path,
                feature_names=feature_names,
                hyperparameters=hyperparameters,
                notes=notes,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def get_champion(self, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = (
                db.query(MLModelRegistry)
                .filter(MLModelRegistry.status == STATUS_CHAMPION)
                .order_by(MLModelRegistry.trained_at.desc())
                .first()
            )
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def get_challengers(self, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            rows = (
                db.query(MLModelRegistry)
                .filter(MLModelRegistry.status == STATUS_CHALLENGER)
                .order_by(MLModelRegistry.model_name)
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()

    def get_latest(self, model_name: str, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = (
                db.query(MLModelRegistry)
                .filter(MLModelRegistry.model_name == model_name)
                .filter(MLModelRegistry.status != STATUS_ARCHIVED)
                .order_by(MLModelRegistry.trained_at.desc())
                .first()
            )
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def list_all(self, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            rows = db.query(MLModelRegistry).order_by(MLModelRegistry.trained_at.desc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()


registry = ModelRegistry()
