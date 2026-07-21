"""Decision-engine calibration dataset: trustworthy-only filtering, bounded
sample-gated proposals, and rollback - never a silent live-weight change."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import CalibrationVersion, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import calibration, outcome

PREFIX = "calib-test-"


def _ledger(pid, direction="LONG", confidence=0.7, source_name="trend", lifecycle_status="RESOLVED_CORRECT",
            expected_edge=0.01, symbol="BTCUSDT", timeframe="5m"):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name=source_name, source_version="1",
        symbol=symbol, timeframe=timeframe, direction=direction, confidence=confidence, expected_edge=expected_edge,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=20),
        resolution_deadline=now - timedelta(minutes=15), reference_price=100.0, lifecycle_status=lifecycle_status,
    )


def _resolution(pid, correct, neutral=False, actual_return=0.01, net_return=0.005):
    return PredictionResolution(
        prediction_id=PREFIX + pid, actual_return=actual_return, resolved_direction="LONG",
        correct=correct, neutral_result=neutral, resolution_reason="test", resolved_at=datetime.now(timezone.utc),
        net_direction_adjusted_return=net_return,
    )


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.query(CalibrationVersion).filter(CalibrationVersion.created_by == "test-runner").delete(synchronize_session=False)
    session.commit()
    yield session
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.query(CalibrationVersion).filter(CalibrationVersion.created_by == "test-runner").delete(synchronize_session=False)
    session.commit()
    session.close()


def test_trustworthy_rows_excludes_pending_and_void(db):
    db.add(_ledger("pending-1", lifecycle_status=outcome.PENDING))
    db.add(_ledger("void-1", lifecycle_status=outcome.VOID_DATA_GAP))
    resolved = _ledger("resolved-1", lifecycle_status=outcome.RESOLVED_CORRECT)
    db.add(resolved)
    db.add(_resolution("resolved-1", correct=True))
    db.commit()
    rows = calibration.trustworthy_rows(db, symbol="BTCUSDT")
    ids = {l.prediction_id for l, _ in rows}
    assert PREFIX + "resolved-1" in ids
    assert PREFIX + "pending-1" not in ids
    assert PREFIX + "void-1" not in ids


def test_compute_metrics_excludes_neutral_from_directional_denominator(db):
    for i in range(3):
        db.add(_ledger(f"win-{i}", lifecycle_status=outcome.RESOLVED_CORRECT))
        db.add(_resolution(f"win-{i}", correct=True))
    db.add(_ledger("neutral-1", lifecycle_status=outcome.RESOLVED_NEUTRAL))
    db.add(_resolution("neutral-1", correct=None, neutral=True))
    db.commit()
    rows = calibration.trustworthy_rows(db, symbol="BTCUSDT")
    metrics = calibration.compute_metrics(rows)
    assert metrics["count"] == 4
    assert metrics["correct"] == 3
    assert metrics["neutral"] == 1
    assert metrics["directional_hit_rate"] == 1.0  # 3/3, neutral excluded from denominator


def test_compute_metrics_empty_set_returns_none_not_zero_division(db):
    metrics = calibration.compute_metrics([])
    assert metrics["count"] == 0
    assert metrics["directional_hit_rate"] is None


def test_propose_calibration_update_skips_sources_below_minimum_sample(db):
    for i in range(5):  # well below MIN_SAMPLE_FOR_CALIBRATION
        db.add(_ledger(f"thin-{i}", source_name="thin_strategy", lifecycle_status=outcome.RESOLVED_CORRECT))
        db.add(_resolution(f"thin-{i}", correct=True))
    db.commit()
    result = calibration.propose_calibration_update(db, created_by="test-runner", current_weights={"thin_strategy": 1.0})
    assert result["weights"]["thin_strategy"] == 1.0  # unchanged - sample too small
    assert result["auto_apply_enabled"] is False


def test_propose_calibration_update_clamps_delta_to_max_per_cycle(db):
    for i in range(60):
        db.add(_ledger(f"strong-{i}", source_name="strong_strategy", lifecycle_status=outcome.RESOLVED_CORRECT))
        db.add(_resolution(f"strong-{i}", correct=True))  # 100% hit rate - would suggest a huge upward move
    db.commit()
    result = calibration.propose_calibration_update(db, created_by="test-runner", current_weights={"strong_strategy": 1.0})
    proposed = result["weights"]["strong_strategy"]
    assert proposed <= 1.0 * (1 + calibration.MAX_WEIGHT_DELTA_PER_CYCLE) + 1e-9
    assert proposed > 1.0  # some positive adjustment did happen


def test_propose_calibration_update_never_applies_automatically(db):
    for i in range(60):
        db.add(_ledger(f"apply-{i}", source_name="apply_strategy", lifecycle_status=outcome.RESOLVED_CORRECT))
        db.add(_resolution(f"apply-{i}", correct=True))
    db.commit()
    result = calibration.propose_calibration_update(db, created_by="test-runner", current_weights={"apply_strategy": 1.0})
    assert result["applied"] is False
    version = db.get(CalibrationVersion, result["version_id"])
    assert version.active is False


def test_rollback_reactivates_previous_version(db):
    v1 = CalibrationVersion(created_by="test-runner", sample_size=100, weights_snapshot={"a": 1.0},
                            metrics_snapshot={}, active=True)
    db.add(v1)
    db.commit()
    v2 = CalibrationVersion(created_by="test-runner", sample_size=100, weights_snapshot={"a": 1.1},
                            metrics_snapshot={}, active=False, previous_version_id=v1.id)
    db.add(v2)
    db.commit()
    v1.active = False
    v2.active = True
    db.commit()
    result = calibration.rollback_calibration(db, to_version_id=v1.id)
    assert result["active_version_id"] == v1.id
    db.refresh(v1)
    db.refresh(v2)
    assert v1.active is True
    assert v2.active is False
    assert v2.rolled_back_at is not None
    db.query(CalibrationVersion).filter(CalibrationVersion.id.in_((v1.id, v2.id))).delete(synchronize_session=False)
    db.commit()


def test_rollback_with_no_prior_version_raises(db):
    with pytest.raises(ValueError):
        calibration.rollback_calibration(db, to_version_id=999999)


def test_compute_metrics_neutral_count_uses_lifecycle_status_not_legacy_field(db):
    """A RESOLVED_NEUTRAL row whose legacy neutral_result boolean was never
    populated (correct=NULL, neutral_result=False) must still count as
    neutral - lifecycle_status is authoritative, the legacy field is not."""
    ledger = _ledger("stale-legacy-neutral", direction="LONG", lifecycle_status=outcome.RESOLVED_NEUTRAL)
    db.add(ledger)
    db.add(_resolution("stale-legacy-neutral", correct=None, neutral=False))
    db.commit()
    rows = calibration.trustworthy_rows(db, source_name="trend")
    metrics = calibration.compute_metrics([r for r in rows if r[0].prediction_id == PREFIX + "stale-legacy-neutral"])
    assert metrics["neutral"] == 1
    assert metrics["correct"] == 0
    assert metrics["wrong"] == 0


def test_directional_breakdown_long_to_neutral_is_non_hit(db):
    db.add(_ledger("long-non-hit", direction="LONG", lifecycle_status=outcome.RESOLVED_NEUTRAL))
    db.add(_resolution("long-non-hit", correct=None, neutral=False))
    db.commit()
    rows = [r for r in calibration.trustworthy_rows(db) if r[0].prediction_id == PREFIX + "long-non-hit"]
    breakdown = calibration.directional_breakdown(rows)
    assert breakdown["directional_non_hit"] == 1
    assert breakdown["correct_abstention"] == 0
    assert breakdown["correct_direction"] == 0
    assert breakdown["wrong_direction"] == 0


def test_directional_breakdown_short_to_neutral_is_non_hit(db):
    db.add(_ledger("short-non-hit", direction="SHORT", lifecycle_status=outcome.RESOLVED_NEUTRAL))
    db.add(_resolution("short-non-hit", correct=None, neutral=False))
    db.commit()
    rows = [r for r in calibration.trustworthy_rows(db) if r[0].prediction_id == PREFIX + "short-non-hit"]
    breakdown = calibration.directional_breakdown(rows)
    assert breakdown["directional_non_hit"] == 1
    assert breakdown["correct_abstention"] == 0


def test_directional_breakdown_no_trade_to_neutral_is_correct_abstention(db):
    db.add(_ledger("abstention", direction="NO_TRADE", lifecycle_status=outcome.RESOLVED_NEUTRAL))
    db.add(_resolution("abstention", correct=None, neutral=False))
    db.commit()
    rows = [r for r in calibration.trustworthy_rows(db) if r[0].prediction_id == PREFIX + "abstention"]
    breakdown = calibration.directional_breakdown(rows)
    assert breakdown["correct_abstention"] == 1
    assert breakdown["directional_non_hit"] == 0


def test_directional_breakdown_correct_and_wrong_unaffected(db):
    db.add(_ledger("dir-correct", direction="LONG", lifecycle_status=outcome.RESOLVED_CORRECT))
    db.add(_resolution("dir-correct", correct=True))
    db.add(_ledger("dir-wrong", direction="LONG", lifecycle_status=outcome.RESOLVED_WRONG))
    db.add(_resolution("dir-wrong", correct=False))
    db.commit()
    rows = [r for r in calibration.trustworthy_rows(db)
            if r[0].prediction_id in (PREFIX + "dir-correct", PREFIX + "dir-wrong")]
    breakdown = calibration.directional_breakdown(rows)
    assert breakdown["correct_direction"] == 1
    assert breakdown["wrong_direction"] == 1
    assert breakdown["directional_non_hit"] == 0
    assert breakdown["correct_abstention"] == 0
