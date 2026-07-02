"""Persists every research run (a benchmark comparison, a walk-forward pass,
an on-demand metrics snapshot, ...) as a versioned, measurable experiment -
the audit trail behind "every prediction must become measurable". Purely
additive: nothing here is read by the live prediction path.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ResearchExperiment
from app.db.session import SessionLocal
from app.ml.model_registry import registry as ml_registry

METRIC_FIELDS = [
    "win_rate",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_pct",
    "cagr_pct",
    "expectancy",
    "average_holding_time_seconds",
    "average_confidence",
    "prediction_accuracy_pct",
]


def current_versions(db: Session | None = None) -> dict:
    champion = ml_registry.get_champion(db=db)
    return {
        "model_version": champion["version"] if champion else "n/a",
        "strategy_version": "adaptive-ensemble-v1",
        "feature_version": "features-v1",
        "market_context_version": "market-intelligence-v1",
    }


def _row_to_dict(row: ResearchExperiment) -> dict:
    return {
        "experiment_id": row.experiment_id,
        "model_version": row.model_version,
        "strategy_version": row.strategy_version,
        "feature_version": row.feature_version,
        "market_context_version": row.market_context_version,
        "start_time": row.start_time.isoformat() if row.start_time else None,
        "end_time": row.end_time.isoformat() if row.end_time else None,
        **{field: getattr(row, field) for field in METRIC_FIELDS},
        "raw_metrics": row.raw_metrics,
        "notes": row.notes,
    }


class ExperimentTracker:
    def start(
        self,
        *,
        model_version: str | None = None,
        strategy_version: str | None = None,
        feature_version: str | None = None,
        market_context_version: str | None = None,
        db: Session | None = None,
    ) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            versions = current_versions(db=db)
            row = ResearchExperiment(
                experiment_id=uuid.uuid4().hex[:12],
                model_version=model_version or versions["model_version"],
                strategy_version=strategy_version or versions["strategy_version"],
                feature_version=feature_version or versions["feature_version"],
                market_context_version=market_context_version or versions["market_context_version"],
                start_time=datetime.now(timezone.utc),
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
        metrics: dict,
        notes: str | None = None,
        db: Session | None = None,
    ) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.query(ResearchExperiment).filter(ResearchExperiment.experiment_id == experiment_id).first()
            if row is None:
                return None

            row.end_time = datetime.now(timezone.utc)
            for field in METRIC_FIELDS:
                if field in metrics:
                    setattr(row, field, metrics[field])
            row.raw_metrics = metrics
            if notes:
                row.notes = notes

            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def record(self, *, metrics: dict, notes: str | None = None, db: Session | None = None, **version_overrides) -> dict:
        """One-shot start+complete for a run whose metrics are already
        computed (e.g. a fresh /api/research/metrics snapshot)."""
        started = self.start(db=db, **version_overrides)
        return self.complete(started["experiment_id"], metrics=metrics, notes=notes, db=db)

    def get(self, experiment_id: str, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.query(ResearchExperiment).filter(ResearchExperiment.experiment_id == experiment_id).first()
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def list_all(self, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            rows = db.query(ResearchExperiment).order_by(ResearchExperiment.start_time.desc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()


tracker = ExperimentTracker()
