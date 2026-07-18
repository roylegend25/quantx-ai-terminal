"""Phase 33: resilient catch-up resolver - structured unresolved reasons,
exponential-backoff backfill, idempotency, and permanent-gap classification.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import SessionLocal
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.decision_engine import resolver
from app.decision_engine.resolver import backfill_overdue_candles, resolve_due


@pytest.fixture(autouse=True)
def _clean():
    yield
    db = SessionLocal()
    try:
        ids = [r.prediction_id for r in db.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like("rc-%"))]
        if ids:
            db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.query(MarketCandle).filter(MarketCandle.symbol.like("RC%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _row(pid, symbol, generated, deadline, timeframe="1m", reference=100.0, direction="LONG"):
    return PredictionLedger(
        prediction_id=f"rc-{pid}", candidate_id=f"rc-cand-{pid}", decision_id=f"rc-dec-{pid}", user_id="rc-user",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="rc_source",
        source_version="1", symbol=symbol, timeframe=timeframe, direction=direction, confidence=0.5,
        target_horizon_seconds=int((deadline - generated).total_seconds()), feature_snapshot_hash=f"h-{pid}",
        generated_at=generated, resolution_deadline=deadline, reference_price=reference,
    )


def _candle(symbol, ts, close, timeframe="1m"):
    return MarketCandle(symbol=symbol, timeframe=timeframe, timestamp=int(ts.timestamp() * 1000),
                        open=close, high=close * 1.001, low=close * 0.999, close=close, volume=1)


# ----------------------------------------------------- structured reasons

def test_missing_entry_price_is_classified_and_never_retried():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("missing-price", "RCAUSDT", now - timedelta(minutes=5), now - timedelta(minutes=1), reference=None))
        db.commit()
        resolve_due(db, limit=50)
        row = db.get(PredictionLedger, "rc-missing-price")
        assert row.unresolved_reason == "missing_entry_price"
        assert db.query(PredictionResolution).filter_by(prediction_id="rc-missing-price").first() is None
    finally:
        db.close()


def test_unsupported_timeframe_is_classified():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("bad-tf", "RCBUSDT", now - timedelta(minutes=5), now - timedelta(minutes=1), timeframe="7m"))
        db.commit()
        resolve_due(db, limit=50)
        row = db.get(PredictionLedger, "rc-bad-tf")
        assert row.unresolved_reason == "unsupported_timeframe"
    finally:
        db.close()


def test_awaiting_horizon_predictions_are_never_touched():
    """Not due yet must never be resolved early - resolve_due's query
    itself excludes them; this proves no reason/attempt state leaks onto a
    row that isn't due."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("not-due", "RCCUSDT", now, now + timedelta(hours=1)))
        db.commit()
        resolve_due(db, limit=50)
        row = db.get(PredictionLedger, "rc-not-due")
        assert row.unresolved_reason is None
        assert row.resolver_attempts in (0, None)
    finally:
        db.close()


def test_gap_with_no_candle_and_no_backfill_attempt_yet_reports_awaiting_future_candle():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("no-candle", "RCDUSDT", now - timedelta(minutes=5), now - timedelta(minutes=1)))
        db.commit()
        resolve_due(db, limit=50)
        row = db.get(PredictionLedger, "rc-no-candle")
        assert row.unresolved_reason == "awaiting_future_candle"
    finally:
        db.close()


# ------------------------------------------------------- backfill + backoff

def test_backfill_fetches_and_resolution_then_succeeds(monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=1)
    generated = now - timedelta(minutes=5)

    async def fake_fetch_klines(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        ts = int(deadline.timestamp() * 1000)
        # Binance kline array layout: [open_time, open, high, low, close, volume, close_time, quote_volume, trades, ...]
        return [[ts, "100.0", "101.0", "99.0", "105.0", "10", ts + 60000, "1000", 5]]

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", fake_fetch_klines)

    db = SessionLocal()
    try:
        db.add(_row("backfill-ok", "RCEUSDT", generated, deadline, reference=100.0))
        db.commit()

        backfilled = asyncio.run(backfill_overdue_candles(db, limit=10))
        assert backfilled == 1

        count = resolve_due(db, limit=50)
        assert count == 1
        resolution = db.query(PredictionResolution).filter_by(prediction_id="rc-backfill-ok").first()
        assert resolution is not None
        assert resolution.resolved_direction == "LONG"  # 105 vs 100 reference clears the neutral band
        row = db.get(PredictionLedger, "rc-backfill-ok")
        assert row.unresolved_reason is None
        assert row.last_resolver_error is None
    finally:
        db.close()


def test_provider_failure_sets_backoff_and_does_not_resolve(monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=1)

    async def failing_fetch(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        raise ConnectionError("simulated provider outage")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", failing_fetch)

    db = SessionLocal()
    try:
        db.add(_row("provider-down", "RCFUSDT", now - timedelta(minutes=5), deadline))
        db.commit()

        backfilled = asyncio.run(backfill_overdue_candles(db, limit=10))
        assert backfilled == 0

        row = db.get(PredictionLedger, "rc-provider-down")
        assert row.resolver_attempts == 1
        assert row.last_resolver_error is not None
        assert row.next_retry_at is not None
        retry_at = row.next_retry_at if row.next_retry_at.tzinfo else row.next_retry_at.replace(tzinfo=timezone.utc)
        assert retry_at > now

        resolve_due(db, limit=50)
        row = db.get(PredictionLedger, "rc-provider-down")
        assert row.unresolved_reason in ("provider_unavailable", "resolver_delayed")
        assert db.query(PredictionResolution).filter_by(prediction_id="rc-provider-down").first() is None
    finally:
        db.close()


def test_backoff_prevents_a_second_attempt_within_the_window(monkeypatch):
    calls = []

    async def counting_fetch(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        calls.append(1)
        raise ConnectionError("still down")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", counting_fetch)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        db.add(_row("backoff", "RCGUSDT", now - timedelta(minutes=5), now - timedelta(minutes=1)))
        db.commit()

        asyncio.run(backfill_overdue_candles(db, limit=10))
        assert len(calls) == 1
        # immediately calling again must respect the just-set backoff window
        asyncio.run(backfill_overdue_candles(db, limit=10))
        assert len(calls) == 1, "a row within its backoff window must not be retried again this soon"
    finally:
        db.close()


def test_permanent_data_gap_after_max_attempts_and_age(monkeypatch):
    async def failing_fetch(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        raise ConnectionError("permanently gapped")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", failing_fetch)
    now = datetime.now(timezone.utc)
    old_generated = now - timedelta(days=10)
    deadline = old_generated + timedelta(minutes=1)

    db = SessionLocal()
    try:
        row = _row("old-gap", "RCHUSDT", old_generated, deadline)
        row.resolver_attempts = resolver.MAX_RETRY_ATTEMPTS_BEFORE_PERMANENT + 1
        row.last_resolver_attempt_at = now - timedelta(hours=2)
        row.next_retry_at = now - timedelta(minutes=1)  # backoff already expired
        db.add(row)
        db.commit()

        asyncio.run(backfill_overdue_candles(db, limit=10))
        db_row = db.get(PredictionLedger, "rc-old-gap")
        assert db_row.unresolved_reason == "permanent_data_gap"

        resolve_due(db, limit=50)
        assert db.query(PredictionResolution).filter_by(prediction_id="rc-old-gap").first() is None
    finally:
        db.close()


# ---------------------------------------------------------------- idempotency

def test_resolution_is_idempotent_across_repeated_calls():
    now = datetime.now(timezone.utc)
    generated = now - timedelta(minutes=5)
    deadline = now - timedelta(minutes=1)
    db = SessionLocal()
    try:
        db.add(_row("idempotent", "RCIUSDT", generated, deadline, reference=100.0))
        db.add(_candle("RCIUSDT", deadline, 110.0))
        db.commit()

        first = resolve_due(db, limit=50)
        second = resolve_due(db, limit=50)
        assert first == 1
        assert second == 0
        assert db.query(PredictionResolution).filter_by(prediction_id="rc-idempotent").count() == 1
    finally:
        db.close()


def test_never_resolves_before_due_at():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("future", "RCJUSDT", now, now + timedelta(hours=1), reference=100.0))
        db.add(_candle("RCJUSDT", now + timedelta(minutes=1), 999.0))  # a candle exists but before due_at
        db.commit()
        resolve_due(db, limit=50)
        assert db.query(PredictionResolution).filter_by(prediction_id="rc-future").first() is None
    finally:
        db.close()


def test_backfill_never_touches_rows_that_are_not_due_yet(monkeypatch):
    calls = []

    async def counting_fetch(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        calls.append(1)
        return []

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", counting_fetch)
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("not-due-backfill", "RCKUSDT", now, now + timedelta(hours=2)))
        db.commit()
        asyncio.run(backfill_overdue_candles(db, limit=10))
        assert len(calls) == 0
    finally:
        db.close()
