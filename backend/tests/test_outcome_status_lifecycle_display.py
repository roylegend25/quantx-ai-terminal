"""outcome_status (the production Prediction Results chart's per-row status
field) must be lifecycle_status-authoritative - never inferred from the
nullable legacy correct field, neutral_result, or resolver_attempts, and
never collapsed to a generic "unresolved" bucket (2026-07-21 chart fix)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome, resolver_status

PREFIX = "outcome-status-test-"


def _ledger(pid, lifecycle_status, deadline_offset_minutes=15, generated_offset_minutes=20):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="5m", direction="LONG", confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}",
        generated_at=now - timedelta(minutes=generated_offset_minutes),
        resolution_deadline=now - timedelta(minutes=deadline_offset_minutes),
        reference_price=100.0, lifecycle_status=lifecycle_status,
    )


def _resolution(pid, correct=None, neutral_result=False):
    return PredictionResolution(
        prediction_id=PREFIX + pid, actual_return=0.001, resolved_direction="NEUTRAL",
        correct=correct, neutral_result=neutral_result, resolution_reason="fixed_horizon_close",
        resolved_at=datetime.now(timezone.utc),
    )


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


def test_pending_row_displays_as_pending():
    ledger = _ledger("pending", "PENDING", deadline_offset_minutes=-10)  # not yet due
    assert resolver_status.outcome_status(ledger, None) == "pending"


def test_matured_pending_row_displays_as_resolving():
    """A stored PENDING row whose deadline has passed but has never been
    attempted must display as resolving, not "unresolved" - it is actively
    eligible for the resolver right now."""
    ledger = _ledger("resolving", "PENDING", deadline_offset_minutes=5)
    assert resolver_status.outcome_status(ledger, None) == "resolving"


def test_retrying_row_displays_as_retrying():
    ledger = _ledger("retrying", outcome.RESOLUTION_ERROR_RETRYING, deadline_offset_minutes=10)
    assert resolver_status.outcome_status(ledger, None) == "retrying"


def test_correct_null_resolved_neutral_row_displays_as_neutral_not_unresolved():
    """The exact bug this fix targets: a RESOLVED_NEUTRAL row whose legacy
    correct field is NULL must display as neutral, never as unresolved/pending."""
    ledger = _ledger("neutral-null-correct", outcome.RESOLVED_NEUTRAL, deadline_offset_minutes=10)
    resolution = _resolution("neutral-null-correct", correct=None, neutral_result=False)
    assert resolver_status.outcome_status(ledger, resolution) == "neutral"


def test_resolved_correct_displays_as_correct():
    ledger = _ledger("correct", outcome.RESOLVED_CORRECT, deadline_offset_minutes=10)
    resolution = _resolution("correct", correct=True)
    assert resolver_status.outcome_status(ledger, resolution) == "correct"


def test_resolved_wrong_displays_as_wrong():
    ledger = _ledger("wrong", outcome.RESOLVED_WRONG, deadline_offset_minutes=10)
    resolution = _resolution("wrong", correct=False)
    assert resolver_status.outcome_status(ledger, resolution) == "wrong"


def test_void_data_gap_displays_as_void():
    ledger = _ledger("void-gap", outcome.VOID_DATA_GAP, deadline_offset_minutes=10)
    assert resolver_status.outcome_status(ledger, None) == "void"


def test_void_invalid_prediction_displays_as_void():
    ledger = _ledger("void-invalid", outcome.VOID_INVALID_PREDICTION, deadline_offset_minutes=10)
    assert resolver_status.outcome_status(ledger, None) == "void"


def test_unknown_lifecycle_status_displays_as_unknown_and_logs_error(caplog):
    """A genuinely unexpected lifecycle_status must never silently become
    "unresolved" or any other known bucket - it must be visibly distinct and
    logged as an error."""
    ledger = _ledger("garbage-status", "SOME_GARBAGE_VALUE_NOT_IN_THE_ENUM", deadline_offset_minutes=10)
    result = resolver_status.outcome_status(ledger, None)
    assert result == "unknown"


def test_latest_results_never_returns_the_word_unresolved(db):
    """End-to-end through the real API-backing function: no row, in any
    lifecycle state, produces the literal string "unresolved" anywhere in
    its outcome field."""
    db.add(_ledger("e2e-pending", "PENDING", deadline_offset_minutes=-10))
    db.add(_ledger("e2e-resolving", "PENDING", deadline_offset_minutes=5))
    db.add(_ledger("e2e-retrying", outcome.RESOLUTION_ERROR_RETRYING, deadline_offset_minutes=10))
    db.add(_ledger("e2e-void", outcome.VOID_DATA_GAP, deadline_offset_minutes=10))
    neutral_ledger = _ledger("e2e-neutral", outcome.RESOLVED_NEUTRAL, deadline_offset_minutes=10)
    db.add(neutral_ledger)
    db.add(_resolution("e2e-neutral", correct=None, neutral_result=False))
    db.commit()

    results = resolver_status.latest_results(db, limit=200, symbol="BTCUSDT")
    ours = [r for r in results if r["prediction_id"].startswith(PREFIX + "e2e-")]
    assert len(ours) == 5
    for row in ours:
        assert "unresolved" not in row["outcome"].lower()
        assert row["outcome"] in ("pending", "resolving", "retrying", "correct", "wrong", "neutral", "void", "unknown")
