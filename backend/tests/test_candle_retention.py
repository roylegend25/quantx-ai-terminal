"""Guarded candle retention/cleanup (main-purpose consolidation, Stage 2).
Verifies the mandatory properties from the task spec: protected candles
survive cleanup, eligible transient candles are deleted, prediction/
performance records are never touched, and run_cleanup refuses to run
without explicit confirmation."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.candle_retention import dry_run_report, run_cleanup
from app.db.models import DailyV2Performance, MarketCandle, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal

NOW = datetime.now(timezone.utc)


def _candle(symbol, timeframe, dt):
    return MarketCandle(
        symbol=symbol, timeframe=timeframe, timestamp=int(dt.timestamp() * 1000),
        open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0, provider="binance_futures",
    )


def _old_resolved_prediction(symbol, timeframe, dt):
    prediction_id = uuid.uuid4().hex
    ledger = PredictionLedger(
        prediction_id=prediction_id, decision_id=uuid.uuid4().hex, candidate_id=uuid.uuid4().hex,
        user_id="retention-test-user", engine="active_drive_v2", engine_version="2.2.0",
        source_type="strategy", source_name="trend", source_version="2.1.0",
        symbol=symbol, timeframe=timeframe, direction="LONG", confidence=70.0, points=1.0,
        data_revision="test", feature_snapshot_hash=uuid.uuid4().hex,
        target_horizon_seconds=900, resolution_deadline=dt + timedelta(seconds=900),
        generated_at=dt, lifecycle_status="RESOLVED_CORRECT",
    )
    resolution = PredictionResolution(
        prediction_id=prediction_id, actual_return=0.01, resolved_direction="LONG",
        correct=True, neutral_result=False, resolution_reason="test",
        resolved_at=dt + timedelta(seconds=1000),
    )
    return ledger, resolution


def _cleanup(db):
    db.query(PredictionResolution).delete()
    db.query(PredictionLedger).delete()
    db.query(DailyV2Performance).delete()
    db.query(MarketCandle).filter(MarketCandle.symbol == "BTCUSDT", MarketCandle.timeframe == "1m").delete()
    db.commit()


def test_recent_candles_within_rolling_buffer_are_always_protected():
    db = SessionLocal()
    try:
        _cleanup(db)
        # Yesterday and today - always inside the mandatory rolling buffer.
        db.add(_candle("BTCUSDT", "1m", NOW - timedelta(hours=2)))
        db.add(_candle("BTCUSDT", "1m", NOW - timedelta(days=1, hours=2)))
        db.commit()

        report = dry_run_report(db=db)
        scope = next(s for s in report["scopes"] if s["symbol"] == "BTCUSDT" and s["timeframe"] == "1m")
        assert scope["eligible_rows"] == 0
        assert scope["protected_rows"] == 2
        # For 1m the timeframe-minimum-retention floor (4 days: max(2,
        # ceil(300s/86400)+3) safety margin) is more conservative than the
        # 2-day rolling buffer, so it's the one that actually binds here -
        # either reason is a correct answer to "why is this protected".
        assert scope["protection_reasons"]
    finally:
        _cleanup(db)
        db.close()


def test_old_candles_are_protected_until_daily_snapshot_exists():
    """An old candle with a resolved prediction but NO daily snapshot yet
    persisted for that date must stay protected (rules 4-7)."""
    db = SessionLocal()
    try:
        _cleanup(db)
        old_dt = NOW - timedelta(days=40)
        db.add(_candle("BTCUSDT", "1m", old_dt))
        ledger, resolution = _old_resolved_prediction("BTCUSDT", "1m", old_dt)
        db.add(ledger)
        db.add(resolution)
        db.commit()

        report = dry_run_report(db=db)
        scope = next(s for s in report["scopes"] if s["symbol"] == "BTCUSDT" and s["timeframe"] == "1m")
        assert scope["eligible_rows"] == 0
        assert scope["protected_rows"] == 1
        assert "daily_snapshot_missing" in scope["protection_reasons"]
    finally:
        _cleanup(db)
        db.close()


def test_old_candles_with_snapshot_and_no_open_predictions_become_eligible():
    db = SessionLocal()
    try:
        _cleanup(db)
        old_dt = NOW - timedelta(days=40)
        db.add(_candle("BTCUSDT", "1m", old_dt))
        ledger, resolution = _old_resolved_prediction("BTCUSDT", "1m", old_dt)
        db.add(ledger)
        db.add(resolution)
        db.add(DailyV2Performance(
            date=old_dt.date(), symbol="BTCUSDT", timeframe="1m", direction="LONG",
            market_regime=None, engine_version="2.2.0", total_eligible_predictions=1,
        ))
        db.commit()

        report = dry_run_report(db=db)
        scope = next(s for s in report["scopes"] if s["symbol"] == "BTCUSDT" and s["timeframe"] == "1m")
        assert scope["eligible_rows"] == 1
        assert scope["protected_rows"] == 0
    finally:
        _cleanup(db)
        db.close()


def test_run_cleanup_refuses_without_explicit_confirmation():
    with pytest.raises(ValueError):
        run_cleanup(confirm=False)


def test_run_cleanup_deletes_only_eligible_candles_and_never_touches_predictions():
    db = SessionLocal()
    try:
        _cleanup(db)
        old_dt = NOW - timedelta(days=40)
        recent_dt = NOW - timedelta(hours=2)
        db.add(_candle("BTCUSDT", "1m", old_dt))
        db.add(_candle("BTCUSDT", "1m", recent_dt))
        ledger, resolution = _old_resolved_prediction("BTCUSDT", "1m", old_dt)
        db.add(ledger)
        db.add(resolution)
        db.add(DailyV2Performance(
            date=old_dt.date(), symbol="BTCUSDT", timeframe="1m", direction="LONG",
            market_regime=None, engine_version="2.2.0", total_eligible_predictions=1,
        ))
        db.commit()

        prediction_count_before = db.query(PredictionLedger).count()
        resolution_count_before = db.query(PredictionResolution).count()

        # The shared test database may carry other tests' old MarketCandle
        # rows across BTCUSDT/ETHUSDT from unrelated fixtures - run_cleanup's
        # global total_deleted is not a reliable signal in that shared
        # state, so assert on this test's own (symbol, timeframe) scope
        # specifically rather than the process-wide count.
        run_cleanup(confirm=True, db=db)

        remaining = db.query(MarketCandle).filter(
            MarketCandle.symbol == "BTCUSDT", MarketCandle.timeframe == "1m",
        ).all()
        assert len(remaining) == 1
        assert remaining[0].timestamp == int(recent_dt.timestamp() * 1000)

        # Predictions, resolutions, and daily snapshots are never touched by
        # candle cleanup.
        assert db.query(PredictionLedger).count() == prediction_count_before
        assert db.query(PredictionResolution).count() == resolution_count_before
        assert db.query(DailyV2Performance).count() == 1
    finally:
        _cleanup(db)
        db.close()
