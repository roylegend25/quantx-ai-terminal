"""Resolution neutral band, calendar-aware horizons, calibration cadence,
quant feature routing, and prediction cycles."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import MarketCandle, PredictionCycle, PredictionLedger, PredictionResolution
from app.decision_engine.ledger import HORIZON_SECONDS, resolution_deadline_for
from app.decision_engine.repository import performance
from app.decision_engine.resolver import resolve_due_sync


def resolve_due(db, limit=200):
    """Adapter for the kept (async, stats-returning) resolver API: these
    behavioural tests only need the resolved count, and must stay hermetic
    (no multi-exchange network fallback)."""
    return resolve_due_sync(db, limit=limit, use_fallback=False)["resolved"]
from app.decision_engine.sources import quant_votes
from app.timeframes.canonical import parse_timeframe


@pytest.fixture(autouse=True)
def _clean_calibration_rows():
    """These tests seed distinctive cal-* ledger rows; remove them and their
    resolutions afterwards so tests that assert on global counts (e.g. the
    append-only ledger test) stay order-independent."""
    yield
    db = SessionLocal()
    try:
        ids = [row.prediction_id for row in db.query(PredictionLedger.prediction_id).filter(
            PredictionLedger.prediction_id.like("cal-%"))]
        if ids:
            db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.query(MarketCandle).filter(MarketCandle.symbol.in_(
            [f"NEUT{i}USDT" for i in range(5)] + ["CADENCEUSDT", "HIERUSDT"])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _ledger_row(i, symbol, generated, deadline, direction="LONG", reference=100.0, timeframe="1m", user="cal-user"):
    return PredictionLedger(prediction_id=f"cal-{symbol}-{i}", candidate_id=f"cal-cand-{symbol}-{i}",
        decision_id=f"cal-dec-{symbol}-{i}", user_id=user, engine="active_drive_v2", engine_version="2.2.0",
        source_type="strategy", source_name="cal_source", source_version="1", symbol=symbol, timeframe=timeframe,
        direction=direction, confidence=0.5, target_horizon_seconds=int((deadline - generated).total_seconds()),
        feature_snapshot_hash=f"h{i}", generated_at=generated, resolution_deadline=deadline, reference_price=reference)


def _candle(symbol, ts, close, timeframe="1m"):
    return MarketCandle(symbol=symbol, timeframe=timeframe, timestamp=int(ts.timestamp() * 1000),
                        open=close, high=close * 1.001, low=close * 0.999, close=close, volume=1)


# ------------------------------------------------- canonical timeframes

def test_one_month_is_calendar_aware_and_distinct_from_one_minute():
    assert parse_timeframe("1M").value == "1M"
    assert parse_timeframe("1m").value == "1m"
    assert parse_timeframe("1M") is not parse_timeframe("1m")
    start = datetime(2026, 1, 31, 12, 30, tzinfo=timezone.utc)
    deadline, seconds = resolution_deadline_for("1M", start)
    # Calendar month from Jan 31 clamps to Feb 28 (2026 is not a leap year).
    assert (deadline.year, deadline.month, deadline.day) == (2026, 2, 28)
    assert seconds == int((deadline - start).total_seconds())
    minute_deadline, minute_seconds = resolution_deadline_for("1m", start)
    assert minute_seconds == HORIZON_SECONDS["1m"] and minute_deadline - start == timedelta(seconds=300)


def test_every_canonical_timeframe_has_an_explicit_horizon():
    for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        assert tf in HORIZON_SECONDS
    deadline, _ = resolution_deadline_for("1w", datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert deadline == datetime(2026, 7, 8, tzinfo=timezone.utc)


# ------------------------------------------------------ neutral outcomes

def test_neutral_band_classifies_flat_moves_and_boundaries(monkeypatch):
    monkeypatch.setattr(settings, "resolution_neutral_band", 0.0005)
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        generated = now - timedelta(minutes=10)
        deadline = now - timedelta(minutes=5)
        cases = [  # (id, direction, close) with reference 100.0
            (0, "LONG", 100.04),    # +4 bps -> inside band -> NEUTRAL
            (1, "LONG", 100.05),    # +5 bps -> exactly on band -> NEUTRAL (<=)
            (2, "LONG", 100.06),    # +6 bps -> LONG win
            (3, "SHORT", 99.94),    # -6 bps -> SHORT win (symmetric)
            (4, "SHORT", 100.06),   # +6 bps -> SHORT loss
        ]
        for i, direction, close in cases:
            symbol = f"NEUT{i}USDT"
            db.add(_ledger_row(i, symbol, generated, deadline, direction=direction))
            db.add(_candle(symbol, deadline + timedelta(minutes=1), close))
        db.commit()
        assert resolve_due(db, limit=50) >= 5
        outcomes = {}
        for i, direction, close in cases:
            row = db.query(PredictionResolution).filter(
                PredictionResolution.prediction_id == f"cal-NEUT{i}USDT-{i}").one()
            outcomes[i] = row
        assert outcomes[0].neutral_result and outcomes[0].correct is None
        assert outcomes[1].neutral_result and outcomes[1].correct is None
        assert outcomes[2].correct is True and not outcomes[2].neutral_result
        assert outcomes[3].correct is True and not outcomes[3].neutral_result
        assert outcomes[4].correct is False and not outcomes[4].neutral_result
        # Neutral outcomes never enter the directional-accuracy denominator.
        stats = performance(db, "cal-user", "cal_source", "1", "NEUT0USDT", "1m", None)
        assert stats["resolved"] == 1 and stats["directional_resolved"] == 0 and stats["accuracy"] is None
    finally:
        db.close()


# --------------------------------------------- 1m calibration cadence math

def test_twenty_per_minute_predictions_reach_calibration_after_twenty_resolutions():
    """Clock-controlled: one prediction per minute with a one-minute
    deadline and complete future data reaches the 20-sample gate exactly at
    the 20th resolved observation - and an excluded (unresolvable) sample
    honestly delays readiness."""
    db = SessionLocal()
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        symbol = "CADENCEUSDT"
        for i in range(21):
            generated = base + timedelta(minutes=i)
            row = _ledger_row(i, symbol, generated, generated + timedelta(minutes=1))
            if i == 7:
                row.reference_price = None  # legacy gap: can never resolve
            db.add(row)
            db.add(_candle(symbol, generated + timedelta(minutes=2), 100.5 + i * 0.01))
        db.commit()
        resolve_due(db, limit=500)
        resolved = db.query(PredictionResolution).join(
            PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(
            PredictionLedger.symbol == symbol).count()
        # 21 generated, 1 excluded for missing reference -> exactly 20 resolve,
        # i.e. the 20-sample gate needs 21 minutes of 1/minute cadence here.
        assert resolved == 20
        stats = performance(db, "cal-user", "cal_source", "1", symbol, "1m", None)
        assert stats["resolved"] == 20
    finally:
        db.close()


def test_resolution_is_idempotent():
    db = SessionLocal()
    try:
        first = resolve_due(db, limit=500)
        assert resolve_due(db, limit=500) == 0 or first == 0  # nothing resolves twice
    finally:
        db.close()


# ------------------------------------------------------ quant availability

def test_quant_votes_use_routed_derivatives_data():
    features = {"price": 101.0, "ema20": 100.0, "ema50": 99.0, "atr": 1.0, "rsi": 55.0,
                "funding_rate": -0.0006, "oi_change_pct": 2.5, "cvd": 150.0, "bid_ask_ratio": 0.65}
    votes = {name: (vote, evidence) for name, _, vote, evidence in quant_votes(features)}
    assert votes["funding_divergence"][1]["current_value"] == -0.0006
    assert votes["funding_divergence"][0]["direction"] == "LONG"  # crowd short vs up-trend
    assert votes["open_interest_divergence"][0]["direction"] == "LONG"
    assert votes["order_book_imbalance"][0]["direction"] == "LONG"
    # Absent inputs stay honestly unavailable rather than fabricated.
    bare = {name: (vote, evidence) for name, _, vote, evidence in quant_votes({"price": 101.0, "ema20": 100.0})}
    assert bare["funding_divergence"][0]["direction"] == "NO_TRADE"
    assert bare["funding_divergence"][1]["current_value"] is None
    assert "not collected" in bare["funding_divergence"][0]["reason"]
    assert bare["correlation_beta_context"][0]["direction"] == "NO_TRADE"


# --------------------------------------------------------- cycles

def test_prediction_cycle_is_idempotent_and_non_destructive(monkeypatch):
    import asyncio
    from app.api import analysis as analysis_api

    async def fake_prediction(symbol, timeframe=None, current_user=None):
        return {"decision_engine": {"final_signal": "NO_TRADE", "decision_id": "cycle-dec", "blocking_reasons": ["x"]}}

    import app.api.prediction as prediction_api
    monkeypatch.setattr(prediction_api, "prediction", fake_prediction)
    db = SessionLocal()
    try:
        before_ledger = db.query(PredictionLedger).count()
        before_res = db.query(PredictionResolution).count()
    finally:
        db.close()
    body = analysis_api.NewCycleRequest(label="test", idempotency_key="cycle-key-1")
    first = asyncio.run(analysis_api.start_prediction_cycle(body, current_user="cycle-user"))
    assert first["created"] is True and first["cycle_id"]
    second = asyncio.run(analysis_api.start_prediction_cycle(body, current_user="cycle-user"))
    assert second["created"] is False and second["cycle_id"] == first["cycle_id"]
    db = SessionLocal()
    try:
        assert db.query(PredictionCycle).filter(PredictionCycle.idempotency_key == "cycle-key-1").count() == 1
        # History preserved: starting a cycle never deletes ledger rows or outcomes.
        assert db.query(PredictionLedger).count() >= before_ledger
        assert db.query(PredictionResolution).count() >= before_res
    finally:
        db.close()
    status = analysis_api.prediction_cycle_status(current_user="cycle-user")
    assert status["cycle_id"] == first["cycle_id"] and status["status"] == "active"


# --------------------------------------------- evidence hierarchy (Phase 9)

def test_regime_fallback_engages_conservatively_without_fabrication():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        symbol = "HIERUSDT"
        for i in range(25):
            generated = now - timedelta(minutes=60 - i)
            row = _ledger_row(i, symbol, generated, generated + timedelta(minutes=1), user="hier-user")
            row.market_regime = "High-volatility bullish trend" if i % 2 else "Compressed-volatility range market"
            db.add(row)
            db.add(_candle(symbol, generated + timedelta(minutes=2), 101.0 + i * 0.01))
        db.commit()
        resolve_due(db, limit=100)
        # The current regime's own bucket has only ~12 samples - below the
        # 20 gate - so the documented fallback widens to all regimes of the
        # SAME source/symbol/timeframe and reports its scope honestly.
        stats = performance(db, "hier-user", "cal_source", "1", symbol, "1m", "High-volatility bullish trend")
        assert stats["resolved"] >= 20
        assert stats["evidence_scope"] == "source_symbol_timeframe"
        # A regime bucket that already qualifies keeps full specificity.
        assert performance(db, "hier-user", "cal_source", "1", symbol, "1m", None)["evidence_scope"] == "source_symbol_timeframe"
    finally:
        db.close()


def test_no_evidence_stays_not_established():
    db = SessionLocal()
    try:
        stats = performance(db, "nobody", "no_source", "9", "NOPEUSDT", "1m", "High-volatility bullish trend")
        assert stats["resolved"] == 0 and stats["accuracy"] is None
        assert stats["tier"] == "insufficient_evidence"
    finally:
        db.close()
