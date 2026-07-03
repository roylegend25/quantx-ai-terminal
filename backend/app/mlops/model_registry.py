"""Model lifecycle registry for the Phase 15 ML platform (GET/POST
/api/models/*). Owns the MLOpsModel table exclusively - independent of
app/ml/model_registry.py, which owns MLModelRegistry and whose Champion row
is always the live adaptive ensemble. Nothing here is read by the live
prediction path (app/api/prediction.py); it exists so trained model
versions can be tracked, promoted, and rolled back through their own
lifecycle without touching production trading logic.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import MLOpsModel
from app.db.session import SessionLocal
from app.mlops.model_version import current_git_commit, next_version

STATUS_TRAINING = "Training"
STATUS_TESTING = "Testing"
STATUS_CHALLENGER = "Challenger"
STATUS_CHAMPION = "Champion"
STATUS_ARCHIVED = "Archived"
STATUS_FAILED = "Failed"

ALL_STATUSES = [
    STATUS_TRAINING,
    STATUS_TESTING,
    STATUS_CHALLENGER,
    STATUS_CHAMPION,
    STATUS_ARCHIVED,
    STATUS_FAILED,
]


def _row_to_dict(row: MLOpsModel) -> dict:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "model_name": row.model_name,
        "algorithm": row.algorithm,
        "version": row.version,
        "status": row.status,
        "trained_at": row.trained_at.isoformat() if row.trained_at else None,
        "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
        "dataset_version": row.dataset_version,
        "feature_version": row.feature_version,
        "train_accuracy": row.train_accuracy,
        "val_accuracy": row.val_accuracy,
        "win_rate": row.win_rate,
        "sharpe_ratio": row.sharpe_ratio,
        "profit_factor": row.profit_factor,
        "max_drawdown_pct": row.max_drawdown_pct,
        "latency_ms": row.latency_ms,
        "training_duration_seconds": row.training_duration_seconds,
        "parameters": row.parameters,
        "feature_list": row.feature_list,
        "git_commit": row.git_commit,
        "model_path": row.model_path,
        "notes": row.notes,
    }


class ModelRegistry:
    def register(
        self,
        *,
        model_name: str,
        algorithm: str | None = None,
        status: str = STATUS_TRAINING,
        dataset_version: str | None = None,
        feature_version: str | None = None,
        train_accuracy: float | None = None,
        val_accuracy: float | None = None,
        win_rate: float | None = None,
        sharpe_ratio: float | None = None,
        profit_factor: float | None = None,
        max_drawdown_pct: float | None = None,
        latency_ms: float | None = None,
        training_duration_seconds: float | None = None,
        parameters: dict | None = None,
        feature_list: list[str] | None = None,
        model_path: str | None = None,
        notes: str | None = None,
        db: Session | None = None,
    ) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = MLOpsModel(
                model_id=uuid.uuid4().hex[:12],
                model_name=model_name,
                algorithm=algorithm,
                version=next_version(model_name, db=db),
                status=status,
                trained_at=datetime.now(timezone.utc),
                dataset_version=dataset_version,
                feature_version=feature_version,
                train_accuracy=train_accuracy,
                val_accuracy=val_accuracy,
                win_rate=win_rate,
                sharpe_ratio=sharpe_ratio,
                profit_factor=profit_factor,
                max_drawdown_pct=max_drawdown_pct,
                latency_ms=latency_ms,
                training_duration_seconds=training_duration_seconds,
                parameters=parameters,
                feature_list=feature_list,
                git_commit=current_git_commit(),
                model_path=model_path,
                notes=notes,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def get_by_id(self, model_id: str, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_row(model_id, db)
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def _get_row(self, model_id: str, db: Session) -> MLOpsModel | None:
        query = db.query(MLOpsModel)
        if str(model_id).isdigit():
            row = query.filter(MLOpsModel.id == int(model_id)).first()
            if row:
                return row
        return query.filter(MLOpsModel.model_id == model_id).first()

    def get_champion(self, model_name: str | None = None, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(MLOpsModel).filter(MLOpsModel.status == STATUS_CHAMPION)
            if model_name:
                query = query.filter(MLOpsModel.model_name == model_name)
            row = query.order_by(MLOpsModel.trained_at.desc()).first()
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def get_challengers(self, model_name: str | None = None, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(MLOpsModel).filter(MLOpsModel.status == STATUS_CHALLENGER)
            if model_name:
                query = query.filter(MLOpsModel.model_name == model_name)
            rows = query.order_by(MLOpsModel.trained_at.desc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()

    def list_versions(self, model_name: str, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            rows = (
                db.query(MLOpsModel)
                .filter(MLOpsModel.model_name == model_name)
                .order_by(MLOpsModel.trained_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()

    def list_all(self, status: str | None = None, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(MLOpsModel)
            if status:
                query = query.filter(MLOpsModel.status == status)
            rows = query.order_by(MLOpsModel.trained_at.desc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()

    def set_status(self, model_id: str, status: str, db: Session | None = None) -> dict | None:
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown status '{status}', expected one of {ALL_STATUSES}")

        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_row(model_id, db)
            if row is None:
                return None

            row.status = status
            if status == STATUS_CHAMPION:
                row.promoted_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def update_metrics(self, model_id: str, db: Session | None = None, **fields) -> dict | None:
        """Merges evaluation-derived metrics (win_rate, sharpe_ratio,
        profit_factor, max_drawdown_pct, val_accuracy, latency_ms) back
        onto a registry row after app/mlops/evaluation.py scores it."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_row(model_id, db)
            if row is None:
                return None

            for key, value in fields.items():
                if value is not None and hasattr(row, key):
                    setattr(row, key, value)

            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def promote_to_champion(self, model_id: str, db: Session | None = None) -> dict | None:
        """Promotes one model version to Champion, archiving whichever
        version (if any) previously held that status for the same
        model_name. Never deletes a row."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_row(model_id, db)
            if row is None:
                return None

            previous_champion = (
                db.query(MLOpsModel)
                .filter(MLOpsModel.model_name == row.model_name, MLOpsModel.status == STATUS_CHAMPION)
                .filter(MLOpsModel.id != row.id)
                .first()
            )
            if previous_champion is not None:
                previous_champion.status = STATUS_ARCHIVED

            row.status = STATUS_CHAMPION
            row.promoted_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()


registry = ModelRegistry()
