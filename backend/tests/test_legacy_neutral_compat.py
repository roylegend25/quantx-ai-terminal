from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import LegacyNeutralCompatCorrection, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import legacy_neutral_compat as lnc
from app.decision_engine import outcome

PREFIX = "lnc-test-"


def _ledger(pid, direction, lifecycle_status=outcome.RESOLVED_NEUTRAL, reference_price=100.0):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="5m", direction=direction, confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=20),
        resolution_deadline=now - timedelta(minutes=15), reference_price=reference_price, lifecycle_status=lifecycle_status,
    )


def _resolution(pid, resolved_direction, correct=None, neutral_result=False, resolution_reason="fixed_horizon_close",
                 actual_return=0.001):
    return PredictionResolution(
        prediction_id=PREFIX + pid, actual_return=actual_return, resolved_direction=resolved_direction,
        correct=correct, neutral_result=neutral_result, resolution_reason=resolution_reason,
        resolved_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(LegacyNeutralCompatCorrection).filter(LegacyNeutralCompatCorrection.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    yield session
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(LegacyNeutralCompatCorrection).filter(LegacyNeutralCompatCorrection.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()


def test_matches_long_prediction_resolved_neutral(db):
    db.add(_ledger("long-neutral", "LONG"))
    db.add(_resolution("long-neutral", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 1
    assert report["long_count"] == 1
    lnc.apply(db)
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "long-neutral").one()
    assert res.neutral_result is True
    assert res.correct is None  # never set correct for a neutral outcome


def test_matches_short_prediction_resolved_neutral(db):
    db.add(_ledger("short-neutral", "SHORT"))
    db.add(_resolution("short-neutral", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 1
    assert report["short_count"] == 1


def test_no_trade_with_neutral_resolved_direction_matches(db):
    """A NO_TRADE prediction whose realised move also landed in the neutral
    band is in scope - resolved_direction=NEUTRAL is satisfied regardless of
    what the prediction's own direction was."""
    db.add(_ledger("notrade-neutral", "NO_TRADE"))
    db.add(_resolution("notrade-neutral", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 1
    assert report["no_trade_count"] == 1


def test_no_trade_with_directional_resolved_price_is_rejected(db):
    """The exact predicate requires resolved_direction=NEUTRAL. A NO_TRADE
    prediction whose realised price actually moved LONG/SHORT is correctly
    RESOLVED_NEUTRAL at the lifecycle level (no directional claim to score),
    but is deliberately out of scope for this narrow legacy-field predicate -
    see module docstring."""
    db.add(_ledger("notrade-directional", "NO_TRADE"))
    db.add(_resolution("notrade-directional", resolved_direction="LONG", correct=None, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0
    assert report["rejected_resolved_direction_not_neutral"] == 1
    lnc.apply(db)
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "notrade-directional").one()
    assert res.neutral_result is False  # untouched - out of scope


def test_already_neutral_result_true_is_not_rematched(db):
    db.add(_ledger("already-true", "LONG"))
    db.add(_resolution("already-true", resolved_direction="NEUTRAL", correct=None, neutral_result=True))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0
    assert report["already_neutral_result_true"] == 1


def test_wrong_lifecycle_status_is_rejected(db):
    """Only lifecycle_status=RESOLVED_NEUTRAL rows are in scope - never
    touch a RESOLVED_CORRECT/WRONG row even if resolved_direction happens to
    be stored as NEUTRAL for some other reason."""
    db.add(_ledger("wrong-status", "LONG", lifecycle_status=outcome.RESOLVED_CORRECT))
    db.add(_resolution("wrong-status", resolved_direction="NEUTRAL", correct=True, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0


def test_wrong_resolution_reason_is_rejected(db):
    db.add(_ledger("other-reason", "LONG"))
    db.add(_resolution("other-reason", resolved_direction="NEUTRAL", correct=None, neutral_result=False,
                        resolution_reason="stop_loss_hit"))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0


def test_missing_reference_price_is_excluded(db):
    db.add(_ledger("no-ref-price", "LONG", reference_price=None))
    db.add(_resolution("no-ref-price", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0


def test_apply_never_changes_lifecycle_status_resolved_direction_or_price(db):
    db.add(_ledger("immutable-check", "SHORT", reference_price=123.45))
    db.add(_resolution("immutable-check", resolved_direction="NEUTRAL", correct=None, neutral_result=False,
                        actual_return=-0.0005))
    db.commit()
    lnc.apply(db)
    ledger = db.get(PredictionLedger, PREFIX + "immutable-check")
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "immutable-check").one()
    assert ledger.lifecycle_status == outcome.RESOLVED_NEUTRAL
    assert ledger.direction == "SHORT"
    assert ledger.reference_price == 123.45
    assert res.resolved_direction == "NEUTRAL"
    assert res.actual_return == -0.0005
    assert res.correct is None


def test_apply_records_audit_row(db):
    db.add(_ledger("audit-check", "LONG"))
    db.add(_resolution("audit-check", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    lnc.apply(db)
    audit = db.query(LegacyNeutralCompatCorrection).filter_by(prediction_id=PREFIX + "audit-check").one()
    assert audit.old_neutral_result is False
    assert audit.new_neutral_result is True
    assert audit.correction_version == lnc.CORRECTION_VERSION
    assert audit.audit_reason == lnc.CORRECTION_VERSION


def test_apply_is_idempotent(db):
    db.add(_ledger("idempotent-check", "LONG"))
    db.add(_resolution("idempotent-check", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    r1 = lnc.apply(db)
    assert r1["corrected_row_count"] == 1
    r2 = lnc.apply(db)
    assert r2["corrected_row_count"] == 0
    # Exactly one audit row, not two.
    count = db.query(LegacyNeutralCompatCorrection).filter_by(prediction_id=PREFIX + "idempotent-check").count()
    assert count == 1


def test_apply_resumable_after_partial_batch(db):
    """Simulates an interruption between batches: a row already corrected
    (has an audit row) must never be re-matched or re-corrected even if its
    neutral_result were somehow reset - the audit table itself is the
    resume/skip marker, independent of the current column value."""
    db.add(_ledger("resume-check", "LONG"))
    db.add(_resolution("resume-check", resolved_direction="NEUTRAL", correct=None, neutral_result=False))
    db.commit()
    lnc.apply(db)
    # Simulate a hypothetical re-flip of the column outside this job.
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "resume-check").one()
    res.neutral_result = False
    db.commit()
    report = lnc.dry_run(db)
    assert report["matched_row_count"] == 0  # excluded via the audit-table marker, not the column value
