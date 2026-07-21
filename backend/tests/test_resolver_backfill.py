from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome
from app.decision_engine.resolver_backfill import backfill_resolved_lifecycle_status

PREFIX = "backfill-test-"


def _ledger(pid, lifecycle_status=None):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="5m", direction="LONG", confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=20),
        resolution_deadline=now - timedelta(minutes=15), reference_price=100.0, lifecycle_status=lifecycle_status,
    )


def _resolution(pid, correct, neutral=False):
    return PredictionResolution(prediction_id=PREFIX + pid, actual_return=0.01, resolved_direction="LONG",
                                correct=correct, neutral_result=neutral, resolution_reason="test",
                                resolved_at=datetime.now(timezone.utc))


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    yield session
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()


def test_backfill_sets_lifecycle_status_from_existing_resolution_without_touching_it(db):
    db.add(_ledger("correct-1", lifecycle_status=None))
    db.add(_resolution("correct-1", correct=True))
    db.add(_ledger("wrong-1", lifecycle_status=None))
    db.add(_resolution("wrong-1", correct=False))
    db.add(_ledger("neutral-1", lifecycle_status=None))
    db.add(_resolution("neutral-1", correct=None, neutral=True))
    db.commit()

    # backfill_resolved_lifecycle_status is intentionally global (it exists
    # to sweep every legacy row, not just one test's) - in the full suite
    # other modules' fixtures can also leave NULL-lifecycle resolved rows,
    # so only assert this test's own 3 rows were backfilled correctly, not
    # an exact global count.
    backfill_resolved_lifecycle_status(db, batch_size=100)

    assert db.get(PredictionLedger, PREFIX + "correct-1").lifecycle_status == outcome.RESOLVED_CORRECT
    assert db.get(PredictionLedger, PREFIX + "wrong-1").lifecycle_status == outcome.RESOLVED_WRONG
    assert db.get(PredictionLedger, PREFIX + "neutral-1").lifecycle_status == outcome.RESOLVED_NEUTRAL
    # The resolution row itself is untouched.
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "correct-1").one()
    assert res.correct is True


def test_backfill_is_idempotent(db):
    db.add(_ledger("idem-1", lifecycle_status=None))
    db.add(_resolution("idem-1", correct=True))
    db.commit()
    backfill_resolved_lifecycle_status(db)
    assert db.get(PredictionLedger, PREFIX + "idem-1").lifecycle_status == outcome.RESOLVED_CORRECT
    # Manually corrupt it the way a real re-run must NOT be able to: if the
    # backfill query re-selected this row, it would overwrite this back to
    # RESOLVED_CORRECT. It must not, because the row is no longer
    # NULL/PENDING.
    db.get(PredictionLedger, PREFIX + "idem-1").lifecycle_status = outcome.RESOLVED_CORRECT
    db.commit()
    backfill_resolved_lifecycle_status(db)
    assert db.get(PredictionLedger, PREFIX + "idem-1").lifecycle_status == outcome.RESOLVED_CORRECT


def test_backfill_never_touches_unresolved_rows(db):
    db.add(_ledger("still-pending-1", lifecycle_status=outcome.PENDING))
    db.commit()
    backfill_resolved_lifecycle_status(db)
    assert db.get(PredictionLedger, PREFIX + "still-pending-1").lifecycle_status == outcome.PENDING


def test_backfill_never_touches_already_terminal_status(db):
    """A row already carrying a lifecycle_status from the live resolver
    (post-repair) must never be overwritten by the backfill, even if it
    somehow also has a resolution row with a different-looking correct
    value - the backfill only targets NULL/PENDING rows."""
    db.add(_ledger("already-set-1", lifecycle_status=outcome.RESOLVED_WRONG))
    db.add(_resolution("already-set-1", correct=True))  # deliberately mismatched
    db.commit()
    backfill_resolved_lifecycle_status(db)
    assert db.get(PredictionLedger, PREFIX + "already-set-1").lifecycle_status == outcome.RESOLVED_WRONG
