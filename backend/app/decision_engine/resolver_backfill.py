"""One-time backfill: populate lifecycle_status on already-resolved
PredictionLedger rows that predate the column (resolved before this
repair). Never touches PredictionResolution or any already-resolved
correct/neutral_result/actual_return value - only sets the new
lifecycle_status column from what is already true of the resolution.
Idempotent: a row already carrying a terminal lifecycle_status is never
revisited (running this twice changes nothing further).
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import PredictionLedger, PredictionResolution
from app.decision_engine import outcome
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.resolver_backfill")


def backfill_resolved_lifecycle_status(db: Session, batch_size: int = 5000) -> int:
    """Sets lifecycle_status = RESOLVED_CORRECT/WRONG/NEUTRAL for every
    already-resolved row still missing it. Returns the number of rows
    updated. Safe to call repeatedly (e.g. from a bounded batch loop) -
    each call only touches rows the previous call didn't reach."""
    total = 0
    for classification, filter_expr in (
        (outcome.RESOLVED_CORRECT, PredictionResolution.correct.is_(True)),
        (outcome.RESOLVED_WRONG, PredictionResolution.correct.is_(False)),
        (outcome.RESOLVED_NEUTRAL, PredictionResolution.correct.is_(None)),
    ):
        while True:
            ids = [
                r[0] for r in db.query(PredictionLedger.prediction_id)
                .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
                .filter(filter_expr, or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
                .limit(batch_size).all()
            ]
            if not ids:
                break
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).update(
                {PredictionLedger.lifecycle_status: classification}, synchronize_session=False)
            db.commit()
            total += len(ids)
            log_event(logger, message="lifecycle_status_backfilled", category="prediction",
                      classification=classification, count=len(ids))
    return total
