"""Resolver repair (2026-07-20): explicit lifecycle status, NO_TRADE
classification fix, cost-aware outcome function, and the two-queue
(recent-priority / historical-backfill) architecture.

Same conventions as test_prediction_resolver_multi_exchange.py: fully
offline, no pytest-asyncio plugin so every async resolver call is driven
via asyncio.run() from a plain sync test function.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome, resolver

PREFIX = "lifecycle-test-"


def _ledger(pid, symbol="BTCUSDT", timeframe="5m", direction="LONG", minutes_ago=20, horizon_min=5, reference_price=100.0):
    now = datetime.now(timezone.utc)
    generated = now - timedelta(minutes=minutes_ago)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="decision-x", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol=symbol, timeframe=timeframe, direction=direction, confidence=0.7, target_horizon_seconds=horizon_min * 60,
        feature_snapshot_hash=f"hash-{pid}", generated_at=generated,
        resolution_deadline=generated + timedelta(minutes=horizon_min),
        reference_price=reference_price, lifecycle_status="PENDING",
    )


def _candle(symbol, timeframe, ts_ms, close):
    return MarketCandle(symbol=symbol, timeframe=timeframe, timestamp=ts_ms, open=close, high=close,
                         low=close, close=close, volume=1.0, provider="binance_futures", quality_score=100.0)


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(
        PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    recent_ms = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000)
    session.query(MarketCandle).filter(MarketCandle.symbol == "LIFECYCLETESTUSDT", MarketCandle.timestamp >= recent_ms).delete(synchronize_session=False)
    session.commit()
    yield session
    session.close()


def _cleanup(db, *pids):
    full = [PREFIX + p for p in pids]
    db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(full)).delete(synchronize_session=False)
    db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(full)).delete(synchronize_session=False)
    db.commit()


# --------------------------------------------------------- outcome.py unit tests

def test_resolve_prediction_outcome_long_correct_and_wrong():
    win = outcome.resolve_prediction_outcome("LONG", 100.0, 102.0, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert win.classification == outcome.RESOLVED_CORRECT
    assert win.net_direction_adjusted_return > 0
    loss = outcome.resolve_prediction_outcome("LONG", 100.0, 98.0, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert loss.classification == outcome.RESOLVED_WRONG


def test_resolve_prediction_outcome_short_correct_and_wrong():
    win = outcome.resolve_prediction_outcome("SHORT", 100.0, 98.0, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert win.classification == outcome.RESOLVED_CORRECT
    loss = outcome.resolve_prediction_outcome("SHORT", 100.0, 102.0, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert loss.classification == outcome.RESOLVED_WRONG


def test_resolve_prediction_outcome_neutral_band_and_costs():
    # Round-trip cost here is fee(0.0005*2) + slippage(3bps/10000*2) = 0.0016.
    # A raw +0.15% move nets to -0.0001 after costs - within the 0.0005
    # neutral band, so a move that looks like a small win before costs must
    # become neutral once realistic costs are subtracted.
    thin = outcome.resolve_prediction_outcome("LONG", 100.0, 100.15, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert thin.classification == outcome.RESOLVED_NEUTRAL
    assert thin.estimated_fee > 0 and thin.estimated_slippage > 0
    # A move too thin to clear costs at all becomes a net loss, not neutral -
    # costs must be able to flip a nominal "win" into a real net loss.
    too_thin = outcome.resolve_prediction_outcome("LONG", 100.0, 100.06, neutral_band=0.0005, fee_rate=0.0005, slippage_bps=3.0)
    assert too_thin.classification == outcome.RESOLVED_WRONG


def test_resolve_prediction_outcome_never_classifies_no_trade_as_directional():
    flat = outcome.resolve_prediction_outcome("NO_TRADE", 100.0, 105.0)
    assert flat.classification == outcome.RESOLVED_NEUTRAL


def test_lifecycle_status_for_attempt_maps_reasons_correctly():
    assert outcome.lifecycle_status_for_attempt("permanent_data_gap") == outcome.VOID_DATA_GAP
    assert outcome.lifecycle_status_for_attempt("legacy_missing_metadata") == outcome.VOID_INVALID_PREDICTION
    assert outcome.lifecycle_status_for_attempt("invalid_due_time") == outcome.VOID_INVALID_PREDICTION
    assert outcome.lifecycle_status_for_attempt("secondary_provider_pending") == outcome.RESOLUTION_ERROR_RETRYING
    assert outcome.lifecycle_status_for_attempt("resolver_error") == outcome.RESOLUTION_ERROR_RETRYING


def test_effective_lifecycle_status_derives_pending_vs_resolving_from_deadline():
    now = datetime.now(timezone.utc)
    assert outcome.effective_lifecycle_status(None, now + timedelta(minutes=5), now) == outcome.PENDING
    assert outcome.effective_lifecycle_status("PENDING", now + timedelta(minutes=5), now) == outcome.PENDING
    assert outcome.effective_lifecycle_status(None, now - timedelta(minutes=5), now) == outcome.RESOLVING
    assert outcome.effective_lifecycle_status("PENDING", now - timedelta(minutes=5), now) == outcome.RESOLVING
    # A terminal/retrying stored status is authoritative regardless of deadline.
    assert outcome.effective_lifecycle_status("RESOLVED_CORRECT", now - timedelta(minutes=5), now) == "RESOLVED_CORRECT"
    assert outcome.effective_lifecycle_status("VOID_DATA_GAP", now + timedelta(minutes=5), now) == "VOID_DATA_GAP"


# --------------------------------------------------------- resolver.py integration tests

def test_no_trade_direction_no_longer_misclassified_as_legacy_missing_metadata(db):
    """The bug: classify_unresolved_reason's direction-validity gate excluded
    NO_TRADE, so any NO_TRADE row that failed one attempt (e.g. no candle
    yet) was mislabeled "legacy_missing_metadata" - implying corruption for
    a perfectly ordinary non-directional cycle."""
    row = _ledger("no-trade-1", direction="NO_TRADE", symbol="LIFECYCLETESTUSDT")
    db.add(row)
    db.commit()
    now = datetime.now(timezone.utc)
    reason = resolver.classify_unresolved_reason(row, now)
    assert reason != "legacy_missing_metadata"
    assert reason == "due_for_resolution"
    _cleanup(db, "no-trade-1")


def test_no_trade_prediction_resolves_successfully_when_candle_available(db):
    row = _ledger("no-trade-2", direction="NO_TRADE", symbol="LIFECYCLETESTUSDT")
    db.add(row)
    db.commit()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=101.0))
    db.commit()
    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["resolved"] == 1
    res = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "no-trade-2").one()
    assert res.correct is None  # never scored as a win or a loss
    updated = db.get(PredictionLedger, PREFIX + "no-trade-2")
    assert updated.lifecycle_status in (outcome.RESOLVED_NEUTRAL,)
    _cleanup(db, "no-trade-2")


def test_lifecycle_status_pending_before_horizon_closes(db):
    row = _ledger("pending-1", minutes_ago=1, horizon_min=60, symbol="LIFECYCLETESTUSDT")
    db.add(row)
    db.commit()
    assert row.lifecycle_status == "PENDING"
    now = datetime.now(timezone.utc)
    assert outcome.effective_lifecycle_status(row.lifecycle_status, row.resolution_deadline, now) == outcome.PENDING
    _cleanup(db, "pending-1")


def test_lifecycle_status_resolving_after_horizon_closes_even_if_never_attempted(db):
    row = _ledger("resolving-1", minutes_ago=20, horizon_min=5, symbol="LIFECYCLETESTUSDT")
    db.add(row)
    db.commit()
    now = datetime.now(timezone.utc)
    assert outcome.effective_lifecycle_status(row.lifecycle_status, row.resolution_deadline, now) == outcome.RESOLVING
    _cleanup(db, "resolving-1")


def test_lifecycle_status_becomes_resolved_correct_on_success(db):
    row = _ledger("resolved-correct-1", direction="LONG", symbol="LIFECYCLETESTUSDT", reference_price=100.0)
    db.add(row)
    db.commit()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=110.0))
    db.commit()
    resolver.resolve_due_sync(db, limit=10)
    updated = db.get(PredictionLedger, PREFIX + "resolved-correct-1")
    assert updated.lifecycle_status == outcome.RESOLVED_CORRECT
    _cleanup(db, "resolved-correct-1")


def test_lifecycle_status_becomes_void_data_gap_after_permanent_gap(db, monkeypatch):
    row = _ledger("void-gap-1", symbol="LIFECYCLETESTUSDT")
    db.add(row)
    db.commit()
    row.resolver_attempts = resolver._PERMANENT_GAP_ATTEMPTS
    row.last_resolver_error = "primary_market_data_gap"
    db.commit()
    now = datetime.now(timezone.utc)
    reason = resolver.classify_unresolved_reason(row, now)
    assert reason == "permanent_data_gap"
    resolver._mark_attempt(row, now, "primary_market_data_gap")
    assert row.lifecycle_status == outcome.VOID_DATA_GAP
    db.commit()
    _cleanup(db, "void-gap-1")


def test_void_statuses_are_terminal_and_excluded_from_trustworthy_set():
    assert outcome.VOID_DATA_GAP not in outcome.TRUSTWORTHY_STATUSES
    assert outcome.VOID_INVALID_PREDICTION not in outcome.TRUSTWORTHY_STATUSES
    assert outcome.PENDING not in outcome.TRUSTWORTHY_STATUSES
    assert outcome.RESOLUTION_ERROR_RETRYING not in outcome.TRUSTWORTHY_STATUSES
    assert outcome.RESOLVED_CORRECT in outcome.TRUSTWORTHY_STATUSES
    assert outcome.VOID_DATA_GAP in outcome.TERMINAL_STATUSES
    assert outcome.VOID_DATA_GAP in outcome.VOID_STATUSES


def test_idempotent_reprocessing_changes_nothing(db):
    row = _ledger("idem-1", direction="SHORT", symbol="LIFECYCLETESTUSDT", reference_price=100.0)
    db.add(row)
    db.commit()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=95.0))
    db.commit()
    first = resolver.resolve_due_sync(db, limit=10)
    assert first["resolved"] == 1
    snapshot = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "idem-1").one()
    first_correct, first_return = snapshot.correct, snapshot.actual_return
    second = resolver.resolve_due_sync(db, limit=10)
    assert second["resolved"] == 0  # unique constraint / already-resolved filter - nothing to redo
    again = db.query(PredictionResolution).filter_by(prediction_id=PREFIX + "idem-1").one()
    assert again.correct == first_correct and again.actual_return == first_return
    _cleanup(db, "idem-1")


# --------------------------------------------------------- two-queue architecture

def test_recent_queue_only_claims_recently_generated_predictions(db):
    recent = _ledger("recent-1", minutes_ago=10, horizon_min=5, symbol="LIFECYCLETESTUSDT")
    old = _ledger("old-1", minutes_ago=600, horizon_min=5, symbol="LIFECYCLETESTUSDT")  # generated 10h ago
    db.add_all([recent, old])
    db.commit()
    for pid in ("recent-1", "old-1"):
        r = db.get(PredictionLedger, PREFIX + pid)
        at_ms = int(r.resolution_deadline.timestamp() * 1000)
        db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=100.5))
    db.commit()
    stats = asyncio.run(resolver.resolve_recent_due(db, limit=10, recent_window_hours=6.0))
    resolved_ids = {r[0] for r in db.query(PredictionResolution.prediction_id).all() if r[0].startswith(PREFIX)}
    assert PREFIX + "recent-1" in resolved_ids
    assert PREFIX + "old-1" not in resolved_ids
    _cleanup(db, "recent-1", "old-1")


def test_historical_queue_only_claims_old_predictions_and_never_touches_recent(db):
    recent = _ledger("recent-2", minutes_ago=10, horizon_min=5, symbol="LIFECYCLETESTUSDT")
    old = _ledger("old-2", minutes_ago=600, horizon_min=5, symbol="LIFECYCLETESTUSDT")
    db.add_all([recent, old])
    db.commit()
    for pid in ("recent-2", "old-2"):
        r = db.get(PredictionLedger, PREFIX + pid)
        at_ms = int(r.resolution_deadline.timestamp() * 1000)
        db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=100.5))
    db.commit()
    stats = asyncio.run(resolver.resolve_historical_backfill(db, limit=10, recent_window_hours=6.0))
    resolved_ids = {r[0] for r in db.query(PredictionResolution.prediction_id).all() if r[0].startswith(PREFIX)}
    assert PREFIX + "old-2" in resolved_ids
    assert PREFIX + "recent-2" not in resolved_ids
    _cleanup(db, "recent-2", "old-2")


def test_large_historical_backlog_never_delays_the_recent_queue(db):
    """The specific requirement: 'the historical backlog must never delay
    current predictions'. Simulate a big old backlog plus one fresh
    prediction and confirm the recent queue resolves the fresh one on its
    own claim, independent of how large the historical queue's claim is."""
    fresh = _ledger("fresh-1", minutes_ago=10, horizon_min=5, symbol="LIFECYCLETESTUSDT")
    db.add(fresh)
    backlog = []
    for i in range(20):
        row = _ledger(f"backlog-{i}", minutes_ago=600 + i, horizon_min=5, symbol="LIFECYCLETESTUSDT")
        db.add(row)
        backlog.append(row)
    db.commit()
    for r in [fresh] + backlog:
        at_ms = int(r.resolution_deadline.timestamp() * 1000)
        db.add(_candle("LIFECYCLETESTUSDT", "5m", at_ms, close=100.5))
    db.commit()
    stats = asyncio.run(resolver.resolve_recent_due(db, limit=5, recent_window_hours=6.0))
    assert stats["resolved"] == 1  # only the fresh one is in the recent window
    resolved_ids = {r[0] for r in db.query(PredictionResolution.prediction_id).all() if r[0].startswith(PREFIX)}
    assert PREFIX + "fresh-1" in resolved_ids
    _cleanup(db, "fresh-1", *[f"backlog-{i}" for i in range(20)])
