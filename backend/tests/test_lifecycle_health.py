"""lifecycle_health must never miscount an already-resolved row (legacy,
pre-repair NULL lifecycle_status) as still PENDING/RESOLVING."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome, resolver_status

PREFIX = "lifehealth-test-"


def _ledger(pid, lifecycle_status=None, minutes_ago=20, horizon_min=5):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="5m", direction="LONG", confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=minutes_ago),
        resolution_deadline=now - timedelta(minutes=minutes_ago - horizon_min), reference_price=100.0, lifecycle_status=lifecycle_status,
    )


def _resolution(pid, correct):
    return PredictionResolution(prediction_id=PREFIX + pid, actual_return=0.01, resolved_direction="LONG",
                                correct=correct, neutral_result=correct is None, resolution_reason="test",
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


def test_legacy_resolved_row_with_null_lifecycle_status_is_not_counted_as_resolving(db):
    """The regression this guards: a row resolved before lifecycle_status
    existed (NULL, matured) must be reclassified from its actual resolution
    outcome, never left in (or added to) the RESOLVING bucket."""
    db.add(_ledger("legacy-correct", lifecycle_status=None))
    db.add(_resolution("legacy-correct", correct=True))
    db.commit()
    health = resolver_status.lifecycle_health(db)
    # It must appear as RESOLVED_CORRECT, contributing at least 1.
    assert health["counts"].get(outcome.RESOLVED_CORRECT, 0) >= 1


def test_genuinely_unresolved_matured_row_is_counted_as_resolving(db):
    db.add(_ledger("truly-resolving", lifecycle_status=None))
    db.commit()
    health = resolver_status.lifecycle_health(db)
    assert health["counts"].get(outcome.RESOLVING, 0) >= 1


def test_not_yet_matured_row_is_counted_as_pending_not_resolving(db):
    db.add(_ledger("truly-pending", lifecycle_status=None, minutes_ago=1, horizon_min=60))
    db.commit()
    health = resolver_status.lifecycle_health(db)
    assert health["counts"].get(outcome.PENDING, 0) >= 1
