"""Status-change audit trail, restart persistence, and concurrent-evaluation
safety for indicator eligibility (Bot Settings Part 4/10/11)."""
import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings as env_settings
from app.db.models import IndicatorEligibility, IndicatorEligibilityHistory, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine.indicator_performance import evaluate_indicator

USER = env_settings.admin_username


def _seed(db, *, source_name, symbol="BTCUSDT", timeframe="5m", n_wrong, n_correct):
    now = datetime.now(timezone.utc)
    total = n_wrong + n_correct
    for i in range(total):
        is_wrong = i < n_wrong
        prediction_id = uuid.uuid4().hex
        gen_at = now - timedelta(minutes=(total - i))
        db.add(PredictionLedger(
            prediction_id=prediction_id, candidate_id=uuid.uuid4().hex, decision_id=uuid.uuid4().hex,
            user_id=USER, engine="active_drive_v2", engine_version="2.2.0", source_type="strategy",
            source_name=source_name, source_version="1.0.0", symbol=symbol, timeframe=timeframe,
            market_regime="test", direction="LONG", probability_up=0.7, probability_down=0.3, confidence=70,
            points=2.0, reference_price=100.0, target_reference_price=102.0, stop_reference_price=99.0,
            data_revision="test", target_horizon_seconds=300, resolution_deadline=gen_at,
            feature_snapshot_hash="test", generated_at=gen_at,
            lifecycle_status="RESOLVED_WRONG" if is_wrong else "RESOLVED_CORRECT", execution_mode="ACTIVE",
        ))
        db.add(PredictionResolution(
            prediction_id=prediction_id, actual_return=0.01, resolved_direction="LONG",
            correct=not is_wrong, neutral_result=False, resolution_reason="test", resolved_at=gen_at,
            net_direction_adjusted_return=0.01 if not is_wrong else -0.01,
        ))
    db.commit()


def _cleanup(db, source_name, symbol, timeframe):
    for mode in ("paper", "binance_real"):
        row = db.query(IndicatorEligibility).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe, mode=mode).first()
        if row:
            db.query(IndicatorEligibilityHistory).filter_by(eligibility_id=row.id).delete()
            db.delete(row)
    db.query(PredictionLedger).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe).delete()
    db.commit()


def test_status_change_audit_trail_has_previous_and_new_status():
    db = SessionLocal()
    name = "audit-test-a"
    try:
        _seed(db, source_name=name, n_wrong=8, n_correct=2)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        history = db.query(IndicatorEligibilityHistory).filter_by(source_name=name).all()
        assert len(history) == 2  # paper + binance_real
        for h in history:
            assert h.previous_status == "ACTIVE"
            assert h.new_status == "SHADOW_ONLY_POOR_PERFORMANCE"
            assert h.trigger_snapshot["wrong"] == 8
            assert h.changed_by == "system"
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_restart_persistence_reads_same_status_from_fresh_session():
    db1 = SessionLocal()
    name = "audit-test-restart"
    try:
        _seed(db1, source_name=name, n_wrong=8, n_correct=2)
        evaluate_indicator(db1, name, "1.0.0", "BTCUSDT", "5m")
    finally:
        db1.close()

    # Simulate a process restart: brand new session, no in-memory state carried over.
    db2 = SessionLocal()
    try:
        row = db2.query(IndicatorEligibility).filter_by(source_name=name, symbol="BTCUSDT", timeframe="5m", mode="paper").first()
        assert row is not None
        assert row.status == "SHADOW_ONLY_POOR_PERFORMANCE"
    finally:
        _cleanup(db2, name, "BTCUSDT", "5m")
        db2.close()


def test_concurrent_evaluations_do_not_double_write_history():
    """Two evaluate_indicator calls against the same identity in sequence
    (simulating a race where both observe the same input data) must not
    produce two history rows for the same transition."""
    db = SessionLocal()
    name = "audit-test-concurrent"
    try:
        _seed(db, source_name=name, n_wrong=8, n_correct=2)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        history = db.query(IndicatorEligibilityHistory).filter_by(source_name=name).all()
        assert len(history) == 2  # one per mode, not duplicated by the second call
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()
