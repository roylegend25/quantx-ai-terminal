"""app.api.analysis's resolved/void/unresolved split must be driven by
lifecycle_status, never by whether PredictionResolution.correct is NULL or
whether a resolution row exists at all - a VOID_* row has no resolution row
either, but it is terminal and must never be counted as still-unresolved."""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.analysis import _is_resolved, _is_unresolved, _is_void
from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome

PREFIX = "analysis-lifecycle-test-"


def _ledger(pid, direction="LONG", lifecycle_status=None):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="5m", direction=direction, confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=20),
        resolution_deadline=now - timedelta(minutes=15), reference_price=100.0, lifecycle_status=lifecycle_status,
    )


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_correct_is_null_resolved_neutral_row_counts_as_resolved_not_unresolved(db):
    """The exact bug this file exists to prevent: a RESOLVED_NEUTRAL row
    with PredictionResolution.correct left NULL must never be treated as
    unresolved just because correct is nullable."""
    ledger = _ledger("null-correct-neutral", lifecycle_status=outcome.RESOLVED_NEUTRAL)
    resolution = PredictionResolution(prediction_id=ledger.prediction_id, actual_return=0.001,
                                       resolved_direction="NEUTRAL", correct=None, neutral_result=False,
                                       resolution_reason="fixed_horizon_close", resolved_at=datetime.now(timezone.utc))
    assert _is_resolved(ledger, resolution) is True
    assert _is_unresolved(ledger, resolution) is False
    assert _is_void(ledger) is False


def test_void_row_is_terminal_not_unresolved(db):
    """A VOID_* row has no PredictionResolution row (same as a genuinely
    still-open row) - only lifecycle_status distinguishes them. VOID must
    never be counted as unresolved."""
    ledger = _ledger("void-row", lifecycle_status=outcome.VOID_INVALID_PREDICTION)
    assert _is_void(ledger) is True
    assert _is_unresolved(ledger, None) is False
    assert _is_resolved(ledger, None) is False


def test_genuinely_pending_row_is_unresolved(db):
    ledger = _ledger("pending-row", lifecycle_status=outcome.PENDING)
    assert _is_unresolved(ledger, None) is True
    assert _is_resolved(ledger, None) is False
    assert _is_void(ledger) is False


def test_retrying_row_is_unresolved_not_void(db):
    ledger = _ledger("retrying-row", lifecycle_status=outcome.RESOLUTION_ERROR_RETRYING)
    assert _is_unresolved(ledger, None) is True
    assert _is_void(ledger) is False


def test_resolved_correct_and_wrong_are_resolved_not_void(db):
    for status in (outcome.RESOLVED_CORRECT, outcome.RESOLVED_WRONG):
        ledger = _ledger(f"resolved-{status}", lifecycle_status=status)
        resolution = PredictionResolution(prediction_id=ledger.prediction_id, actual_return=0.01,
                                           resolved_direction="LONG", correct=(status == outcome.RESOLVED_CORRECT),
                                           neutral_result=False, resolution_reason="fixed_horizon_close",
                                           resolved_at=datetime.now(timezone.utc))
        assert _is_resolved(ledger, resolution) is True
        assert _is_void(ledger) is False
        assert _is_unresolved(ledger, resolution) is False


def test_legacy_null_lifecycle_status_with_resolution_falls_back_to_resolved(db):
    """A row that predates the lifecycle_status column but already has a
    resolution row is treated as resolved (matches what it will read once
    backfilled), not silently miscounted as still open."""
    ledger = _ledger("legacy-null-status", lifecycle_status=None)
    resolution = PredictionResolution(prediction_id=ledger.prediction_id, actual_return=0.01,
                                       resolved_direction="LONG", correct=True, neutral_result=False,
                                       resolution_reason="fixed_horizon_close", resolved_at=datetime.now(timezone.utc))
    assert _is_resolved(ledger, resolution) is True
    assert _is_unresolved(ledger, resolution) is False


def test_legacy_null_lifecycle_status_without_resolution_is_unresolved(db):
    ledger = _ledger("legacy-null-status-open", lifecycle_status=None)
    assert _is_resolved(ledger, None) is False
    assert _is_unresolved(ledger, None) is True


def test_resolved_void_unresolved_partition_is_exhaustive_and_disjoint():
    """Every (lifecycle_status, has_resolution) combination that can occur
    lands in exactly one of resolved/void/unresolved - never zero, never
    two."""
    cases = [
        (outcome.PENDING, False), (outcome.RESOLVING, False), (outcome.RESOLUTION_ERROR_RETRYING, False),
        (outcome.RESOLVED_CORRECT, True), (outcome.RESOLVED_WRONG, True), (outcome.RESOLVED_NEUTRAL, True),
        (outcome.VOID_DATA_GAP, False), (outcome.VOID_INVALID_PREDICTION, False),
        (None, True), (None, False),
    ]
    for status, has_resolution in cases:
        ledger = _ledger(f"partition-{status}-{has_resolution}", lifecycle_status=status)
        resolution = object() if has_resolution else None
        buckets = [_is_resolved(ledger, resolution), _is_void(ledger), _is_unresolved(ledger, resolution)]
        assert sum(buckets) == 1, f"status={status} has_resolution={has_resolution} landed in {sum(buckets)} buckets"
