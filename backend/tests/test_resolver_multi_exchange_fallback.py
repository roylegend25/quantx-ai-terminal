"""Multi-exchange fallback added to the Phase 33 resolver
(app/decision_engine/resolver.py's _try_multi_exchange_fallback) plus the
new Prediction Results read-paths (resolver_status.py).

Fully offline - provider network calls are monkeypatched, never real httpx
requests. No pytest-asyncio plugin in this codebase, so every test is a
plain sync function (asyncio.run for the resolver's async entrypoints),
matching test_resolver_catchup.py's established pattern."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.data_sources import resolution_providers as providers
from app.data_sources import symbol_map
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import resolver, resolver_status
from app.decision_engine.resolver import backfill_overdue_candles, resolve_due


@pytest.fixture(autouse=True)
def _clean():
    yield
    db = SessionLocal()
    try:
        ids = [r.prediction_id for r in db.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like("mef-%"))]
        if ids:
            db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _row(pid, symbol, generated, deadline, timeframe="5m", reference=100.0, direction="LONG"):
    return PredictionLedger(
        prediction_id=f"mef-{pid}", candidate_id=f"mef-cand-{pid}", decision_id=f"mef-dec-{pid}", user_id="mef-user",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="mef_source",
        source_version="1", symbol=symbol, timeframe=timeframe, direction=direction, confidence=0.5,
        target_horizon_seconds=int((deadline - generated).total_seconds()), feature_snapshot_hash=f"h-{pid}",
        generated_at=generated, resolution_deadline=deadline, reference_price=reference,
    )


def _failing_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
    raise AssertionError("should never be called")


async def _empty_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
    return []


def test_btc_falls_back_to_bybit_when_binance_has_no_candles(monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=1)
    generated = now - timedelta(minutes=6)

    async def empty_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        return []

    async def fake_bybit(symbol, timeframe, at_ms):
        return providers.ResolutionPriceObservation("bybit", "bybit", "usdt_perp", symbol, at_ms,
                                                      actual_market_timestamp=at_ms, price=101.0, confidence=0.9)

    async def dead(symbol, timeframe, at_ms):
        return providers.ResolutionPriceObservation("x", "x", "x", symbol, at_ms, error="timeout")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", empty_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": None, "bybit": fake_bybit, "okx": dead, "hyperliquid": dead, "binance_spot": dead})

    db = SessionLocal()
    try:
        db.add(_row("btc-fallback", "BTCUSDT", generated, deadline))
        db.commit()
        backfilled = asyncio.run(backfill_overdue_candles(db, limit=10))
        assert backfilled == 1
        resolved = resolve_due(db, limit=10)
        assert resolved == 1
        res = db.query(PredictionResolution).filter_by(prediction_id="mef-btc-fallback").one()
        assert res.fallback_used is True
        assert res.resolution_provider == "bybit"
        assert res.resolved_price == 101.0
    finally:
        db.close()


def test_provider_disagreement_keeps_row_unresolved(monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=1)
    generated = now - timedelta(minutes=6)

    async def empty_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        return []

    async def fake_bybit(symbol, timeframe, at_ms):
        return providers.ResolutionPriceObservation("bybit", "bybit", "usdt_perp", symbol, at_ms,
                                                      actual_market_timestamp=at_ms, price=100.0, confidence=0.9)

    async def fake_okx(symbol, timeframe, at_ms):
        return providers.ResolutionPriceObservation("okx", "okx", "usdt_swap", symbol, at_ms,
                                                      actual_market_timestamp=at_ms, price=105.0, confidence=0.9)  # 5% away

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", empty_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": None, "bybit": fake_bybit, "okx": fake_okx, "hyperliquid": fake_okx, "binance_spot": fake_okx})

    db = SessionLocal()
    try:
        db.add(_row("eth-disagree", "ETHUSDT", generated, deadline))
        db.commit()
        backfilled = asyncio.run(backfill_overdue_candles(db, limit=10))
        assert backfilled == 0
        row = db.get(PredictionLedger, "mef-eth-disagree")
        assert row.last_resolver_error == "provider_disagreement"
        resolve_due(db, limit=10)
        row = db.get(PredictionLedger, "mef-eth-disagree")
        # Matches test_resolver_catchup.py::test_provider_failure_sets_backoff_and_does_not_resolve's
        # existing tolerance: resolve_due's own backoff check can overwrite a
        # freshly-classified reason with "resolver_delayed" once next_retry_at
        # is in the future, which backfill_overdue_candles always sets on failure.
        assert row.unresolved_reason in ("exchange_price_disagreement", "resolver_delayed")
        assert db.query(PredictionResolution).filter_by(prediction_id="mef-eth-disagree").count() == 0
    finally:
        db.close()


def test_non_btc_eth_symbols_never_trigger_fallback_network_calls(monkeypatch):
    """Scope guard: a symbol outside symbol_map.CANONICAL_SYMBOLS must never
    reach a fallback provider, preserving test_resolver_catchup.py's
    hermetic-by-default assumption for every other symbol in this suite."""
    now = datetime.now(timezone.utc)
    called = {"n": 0}

    async def empty_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        return []

    async def spy(symbol, timeframe, at_ms):
        called["n"] += 1
        return providers.ResolutionPriceObservation("x", "x", "x", symbol, at_ms, error="should not be called")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", empty_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": None, "bybit": spy, "okx": spy, "hyperliquid": spy, "binance_spot": spy})

    db = SessionLocal()
    try:
        db.add(_row("sol-noscope", "SOLUSDT", now - timedelta(minutes=6), now - timedelta(minutes=1)))
        db.commit()
        asyncio.run(backfill_overdue_candles(db, limit=10))
        assert called["n"] == 0
    finally:
        db.close()


def test_fallback_never_used_when_binance_succeeds(monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=1)
    generated = now - timedelta(minutes=6)
    ts = int(deadline.timestamp() * 1000)

    async def ok_binance(symbol, interval, start_ms=None, end_ms=None, limit=1000):
        return [[ts, "100.0", "101.0", "99.0", "102.0", "10", ts + 60000, "1000", 5]]

    called = {"n": 0}

    async def spy(symbol, timeframe, at_ms):
        called["n"] += 1
        return providers.ResolutionPriceObservation("x", "x", "x", symbol, at_ms, error="should not be called")

    monkeypatch.setattr("app.data_sources.binance_futures.fetch_klines", ok_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": None, "bybit": spy, "okx": spy, "hyperliquid": spy, "binance_spot": spy})

    db = SessionLocal()
    try:
        db.add(_row("btc-noneed", "BTCUSDT", generated, deadline))
        db.commit()
        asyncio.run(backfill_overdue_candles(db, limit=10))
        assert called["n"] == 0
        resolve_due(db, limit=10)
        res = db.query(PredictionResolution).filter_by(prediction_id="mef-btc-noneed").one()
        assert res.fallback_used is False
        assert res.resolution_provider == "binance_futures"
    finally:
        db.close()


def test_1M_timeframe_never_attempts_fallback(monkeypatch):
    called = {"n": 0}

    async def spy(symbol, timeframe, at_ms):
        called["n"] += 1
        return providers.ResolutionPriceObservation("x", "x", "x", symbol, at_ms, error="should not be called")

    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": None, "bybit": spy, "okx": spy, "hyperliquid": spy, "binance_spot": spy})
    now = datetime.now(timezone.utc)
    row = _row("btc-1M", "BTCUSDT", now - timedelta(days=40), now - timedelta(days=1), timeframe="1M")
    db = SessionLocal()
    try:
        ok, error = asyncio.run(resolver._try_multi_exchange_fallback(db, row, int((now - timedelta(days=1)).timestamp() * 1000)))
        assert ok is False
        assert called["n"] == 0
    finally:
        db.close()


def test_outcome_status_never_green_or_red_when_unresolved():
    now = datetime.now(timezone.utc)
    future = _row("status-future", "BTCUSDT", now - timedelta(minutes=1), now + timedelta(minutes=30))
    overdue = _row("status-overdue", "BTCUSDT", now - timedelta(minutes=30), now - timedelta(minutes=5))
    overdue.resolver_attempts = 2
    assert resolver_status.outcome_status(future, None, now) == "unresolved_not_due"
    assert resolver_status.outcome_status(overdue, None, now) == "overdue_provider_error"


def test_accuracy_summary_scoped_to_btc_eth_only():
    db = SessionLocal()
    try:
        summary = resolver_status.accuracy_summary(db)
        assert set(summary["by_symbol"].keys()) <= {"BTCUSDT", "ETHUSDT"}
        assert summary["combined"] is not None
    finally:
        db.close()


def test_latest_results_bounded_to_limit():
    db = SessionLocal()
    try:
        rows = resolver_status.latest_results(db, limit=10)
        assert len(rows) <= 10
    finally:
        db.close()


def test_catchup_never_creates_a_trade():
    from app.db.models import Trade
    db = SessionLocal()
    try:
        before = db.query(Trade).count()
        now = datetime.now(timezone.utc)
        db.add(_row("no-trade", "BTCUSDT", now - timedelta(minutes=6), now - timedelta(minutes=1)))
        db.flush()
        db.add(MarketCandle(symbol="BTCUSDT", timeframe="5m", timestamp=int((now - timedelta(minutes=1)).timestamp() * 1000),
                            open=101, high=102, low=100, close=101, volume=1, provider="binance_futures"))
        db.commit()
        resolve_due(db, limit=10)
        after = db.query(Trade).count()
        assert after == before
    finally:
        db.close()


def test_resolver_module_never_imports_execution_path():
    import app.decision_engine.resolver as resolver_module
    with open(resolver_module.__file__) as f:
        content = f.read()
    assert "execution_router" not in content
    assert "BinanceExecutionProvider" not in content
