"""Poor-performance rule and its safeguards (Bot Settings Part 4/10):
- >=10 valid resolved samples required before the rule evaluates at all
- 7+ of the latest 10 wrong -> SHADOW_ONLY_POOR_PERFORMANCE, for that exact
  (symbol, timeframe) only, in both paper and binance_real modes at once
- PENDING/RESOLVING/RESOLUTION_ERROR_RETRYING/VOID_* never count
- neutral outcomes are tracked separately, never as "wrong"
- idempotent, cooldown-respecting, audited
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings as env_settings
from app.db.models import IndicatorEligibility, IndicatorEligibilityHistory, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import governance_repository, indicator_notifications
from app.decision_engine.indicator_performance import evaluate_indicator

USER = env_settings.admin_username


def _seed(db, *, source_name, source_version="1.0.0", symbol="BTCUSDT", timeframe="5m",
          outcomes, execution_mode="ACTIVE"):
    """outcomes: list of "correct"|"wrong"|"neutral"|"pending"|"void", oldest
    first (last element is the most recent)."""
    now = datetime.now(timezone.utc)
    lifecycle_map = {
        "correct": "RESOLVED_CORRECT", "wrong": "RESOLVED_WRONG", "neutral": "RESOLVED_NEUTRAL",
        "pending": "PENDING", "retrying": "RESOLUTION_ERROR_RETRYING", "void": "VOID_DATA_GAP",
    }
    for i, outcome in enumerate(outcomes):
        prediction_id = uuid.uuid4().hex
        gen_at = now - timedelta(minutes=(len(outcomes) - i))
        lifecycle = lifecycle_map[outcome]
        db.add(PredictionLedger(
            prediction_id=prediction_id, candidate_id=uuid.uuid4().hex, decision_id=uuid.uuid4().hex,
            user_id=USER, engine="active_drive_v2", engine_version="2.2.0", source_type="strategy",
            source_name=source_name, source_version=source_version, symbol=symbol, timeframe=timeframe,
            market_regime="test", direction="LONG", probability_up=0.7, probability_down=0.3, confidence=70,
            points=2.0, reference_price=100.0, target_reference_price=102.0, stop_reference_price=99.0,
            data_revision="test", target_horizon_seconds=300, resolution_deadline=gen_at,
            feature_snapshot_hash="test", generated_at=gen_at, lifecycle_status=lifecycle, execution_mode=execution_mode,
        ))
        if lifecycle in ("RESOLVED_CORRECT", "RESOLVED_WRONG", "RESOLVED_NEUTRAL"):
            db.add(PredictionResolution(
                prediction_id=prediction_id, actual_return=0.01, resolved_direction="LONG",
                correct=(True if lifecycle == "RESOLVED_CORRECT" else False if lifecycle == "RESOLVED_WRONG" else None),
                neutral_result=(lifecycle == "RESOLVED_NEUTRAL"), resolution_reason="test", resolved_at=gen_at,
                net_direction_adjusted_return=0.01 if lifecycle == "RESOLVED_CORRECT" else -0.01,
            ))
    db.commit()


def _cleanup(db, source_name, symbol, timeframe):
    for mode in ("paper", "binance_real"):
        row = db.query(IndicatorEligibility).filter_by(
            source_name=source_name, symbol=symbol, timeframe=timeframe, mode=mode).first()
        if row:
            db.query(IndicatorEligibilityHistory).filter_by(eligibility_id=row.id).delete()
            db.delete(row)
    db.query(PredictionLedger).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe).delete()
    db.commit()


def test_six_of_ten_wrong_stays_active():
    db = SessionLocal()
    try:
        outcomes = ["wrong"] * 6 + ["correct"] * 4
        _seed(db, source_name="rsi_momentum", symbol="BTCUSDT", timeframe="5m", outcomes=outcomes)
        result = evaluate_indicator(db, "rsi_momentum", "1.0.0", "BTCUSDT", "5m")
        assert result["paper"]["status"] == "ACTIVE"
        assert result["binance_real"]["status"] == "ACTIVE"
    finally:
        _cleanup(db, "rsi_momentum", "BTCUSDT", "5m")
        db.close()


def test_seven_of_ten_wrong_becomes_shadow_only_both_modes():
    db = SessionLocal()
    try:
        outcomes = ["wrong"] * 7 + ["correct"] * 3
        _seed(db, source_name="rsi_momentum", symbol="BTCUSDT", timeframe="5m", outcomes=outcomes)
        result = evaluate_indicator(db, "rsi_momentum", "1.0.0", "BTCUSDT", "5m")
        assert result["paper"]["status"] == "SHADOW_ONLY_POOR_PERFORMANCE"
        assert result["binance_real"]["status"] == "SHADOW_ONLY_POOR_PERFORMANCE"
        history = db.query(IndicatorEligibilityHistory).filter_by(source_name="rsi_momentum").all()
        assert len(history) == 2  # one per mode
        assert all(h.new_status == "SHADOW_ONLY_POOR_PERFORMANCE" for h in history)
    finally:
        _cleanup(db, "rsi_momentum", "BTCUSDT", "5m")
        db.close()


def test_insufficient_sample_below_ten():
    db = SessionLocal()
    try:
        outcomes = ["wrong"] * 5
        _seed(db, source_name="macd_momentum", symbol="BTCUSDT", timeframe="5m", outcomes=outcomes)
        result = evaluate_indicator(db, "macd_momentum", "1.0.0", "BTCUSDT", "5m")
        assert result["paper"]["status"] == "INSUFFICIENT_SAMPLE"
    finally:
        _cleanup(db, "macd_momentum", "BTCUSDT", "5m")
        db.close()


def test_pending_retrying_void_excluded_from_window():
    """Interspersed PENDING/RESOLUTION_ERROR_RETRYING/VOID_* rows must never
    be counted as correct or wrong, and must not consume a slot in the
    latest-10 trustworthy window."""
    db = SessionLocal()
    try:
        # 7 wrong (trustworthy) + a bunch of untrustworthy noise that must be ignored entirely.
        outcomes = ["pending", "void", "retrying"] + ["wrong"] * 7 + ["correct"] * 3
        _seed(db, source_name="atr_breakout", symbol="BTCUSDT", timeframe="5m", outcomes=outcomes)
        result = evaluate_indicator(db, "atr_breakout", "1.0.0", "BTCUSDT", "5m")
        assert result["paper"]["status"] == "SHADOW_ONLY_POOR_PERFORMANCE"
    finally:
        _cleanup(db, "atr_breakout", "BTCUSDT", "5m")
        db.close()


def test_neutral_tracked_separately_never_counted_as_wrong():
    db = SessionLocal()
    try:
        # 4 wrong + 6 neutral: wrong_rate is 4/10, well under the 7/10 bar,
        # even though only correct+wrong=4 are "directional".
        outcomes = ["wrong"] * 4 + ["neutral"] * 6
        _seed(db, source_name="bollinger_breakout", symbol="BTCUSDT", timeframe="5m", outcomes=outcomes)
        result = evaluate_indicator(db, "bollinger_breakout", "1.0.0", "BTCUSDT", "5m")
        assert result["paper"]["status"] == "ACTIVE"
    finally:
        _cleanup(db, "bollinger_breakout", "BTCUSDT", "5m")
        db.close()


def test_poor_performance_on_5m_does_not_disable_15m():
    db = SessionLocal()
    try:
        _seed(db, source_name="volume_anomaly", symbol="BTCUSDT", timeframe="5m", outcomes=["wrong"] * 7 + ["correct"] * 3)
        _seed(db, source_name="volume_anomaly", symbol="BTCUSDT", timeframe="15m", outcomes=["correct"] * 10)
        evaluate_indicator(db, "volume_anomaly", "1.0.0", "BTCUSDT", "5m")
        evaluate_indicator(db, "volume_anomaly", "1.0.0", "BTCUSDT", "15m")
        row_5m = db.query(IndicatorEligibility).filter_by(source_name="volume_anomaly", symbol="BTCUSDT", timeframe="5m", mode="paper").first()
        row_15m = db.query(IndicatorEligibility).filter_by(source_name="volume_anomaly", symbol="BTCUSDT", timeframe="15m", mode="paper").first()
        assert row_5m.status == "SHADOW_ONLY_POOR_PERFORMANCE"
        assert row_15m.status == "ACTIVE"
    finally:
        _cleanup(db, "volume_anomaly", "BTCUSDT", "5m")
        _cleanup(db, "volume_anomaly", "BTCUSDT", "15m")
        db.close()


def test_poor_btc_performance_does_not_disable_eth():
    db = SessionLocal()
    try:
        _seed(db, source_name="funding_divergence", symbol="BTCUSDT", timeframe="5m", outcomes=["wrong"] * 8 + ["correct"] * 2)
        _seed(db, source_name="funding_divergence", symbol="ETHUSDT", timeframe="5m", outcomes=["correct"] * 10)
        evaluate_indicator(db, "funding_divergence", "1.0.0", "BTCUSDT", "5m")
        evaluate_indicator(db, "funding_divergence", "1.0.0", "ETHUSDT", "5m")
        row_btc = db.query(IndicatorEligibility).filter_by(source_name="funding_divergence", symbol="BTCUSDT", timeframe="5m", mode="paper").first()
        row_eth = db.query(IndicatorEligibility).filter_by(source_name="funding_divergence", symbol="ETHUSDT", timeframe="5m", mode="paper").first()
        assert row_btc.status == "SHADOW_ONLY_POOR_PERFORMANCE"
        assert row_eth.status == "ACTIVE"
    finally:
        _cleanup(db, "funding_divergence", "BTCUSDT", "5m")
        _cleanup(db, "funding_divergence", "ETHUSDT", "5m")
        db.close()


def test_idempotent_reevaluation_no_duplicate_history_or_notification():
    db = SessionLocal()
    try:
        _seed(db, source_name="ema_pullback", symbol="BTCUSDT", timeframe="5m", outcomes=["wrong"] * 8 + ["correct"] * 2)
        evaluate_indicator(db, "ema_pullback", "1.0.0", "BTCUSDT", "5m")
        history_after_first = db.query(IndicatorEligibilityHistory).filter_by(source_name="ema_pullback").count()
        notifications_after_first = indicator_notifications.list_notifications(db=db)["notifications"]
        matching_first = [n for n in notifications_after_first if n["source_name"] == "ema_pullback"]

        # Re-run with no new data - must be a pure no-op.
        evaluate_indicator(db, "ema_pullback", "1.0.0", "BTCUSDT", "5m")
        history_after_second = db.query(IndicatorEligibilityHistory).filter_by(source_name="ema_pullback").count()
        notifications_after_second = indicator_notifications.list_notifications(db=db)["notifications"]
        matching_second = [n for n in notifications_after_second if n["source_name"] == "ema_pullback"]

        assert history_after_second == history_after_first
        assert len(matching_second) == len(matching_first)
    finally:
        _cleanup(db, "ema_pullback", "BTCUSDT", "5m")
        db.close()


def test_cooldown_blocks_reflip_within_window():
    db = SessionLocal()
    try:
        governance_repository.update_settings({"status_change_cooldown_hours": 24.0}, db=db)
        _seed(db, source_name="rsi_extreme_reversal", symbol="BTCUSDT", timeframe="5m", outcomes=["wrong"] * 8 + ["correct"] * 2)
        evaluate_indicator(db, "rsi_extreme_reversal", "1.0.0", "BTCUSDT", "5m")
        row = db.query(IndicatorEligibility).filter_by(source_name="rsi_extreme_reversal", symbol="BTCUSDT", timeframe="5m", mode="paper").first()
        assert row.status == "SHADOW_ONLY_POOR_PERFORMANCE"

        # Manually flip back to ACTIVE to simulate a reactivation, then feed
        # in fresh poor-performance data immediately - cooldown must prevent
        # an instant re-flip back to shadow-only.
        row.status = "ACTIVE"
        row.last_status_change_at = datetime.now(timezone.utc)
        db.commit()
        _seed(db, source_name="rsi_extreme_reversal", symbol="BTCUSDT", timeframe="5m", outcomes=["wrong"] * 8 + ["correct"] * 2)
        evaluate_indicator(db, "rsi_extreme_reversal", "1.0.0", "BTCUSDT", "5m")
        db.refresh(row)
        assert row.status == "ACTIVE"  # cooldown held
    finally:
        _cleanup(db, "rsi_extreme_reversal", "BTCUSDT", "5m")
        governance_repository.update_settings({"status_change_cooldown_hours": governance_repository.DEFAULTS["status_change_cooldown_hours"]}, db=db)
        db.close()
