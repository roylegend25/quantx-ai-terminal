"""Unresolved-prediction pipeline rebuild: multi-exchange resolver, structured
classification, and the new accuracy/dashboard read-paths.

Every test here is fully offline - provider network calls are monkeypatched,
never real httpx requests - so this suite stays deterministic and fast.
This codebase has no pytest-asyncio/anyio plugin (see resolve_due_sync's
docstring), so every test is a plain sync function using resolve_due_sync -
an `async def test_*` here would silently never execute its body."""
from datetime import datetime, timedelta, timezone

import pytest

from app.data_sources import resolution_providers as providers
from app.data_sources import symbol_map
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import resolver, resolver_status


def _ledger(symbol="BTCUSDT", timeframe="5m", direction="LONG", minutes_ago=20, horizon_min=5,
            prediction_id=None, reference_price=100.0, target=None, stop=None, engine="active_drive_v2",
            source_name="trend", source_type="strategy"):
    now = datetime.now(timezone.utc)
    generated = now - timedelta(minutes=minutes_ago)
    pid = prediction_id or f"pred-{symbol}-{generated.timestamp()}"
    return PredictionLedger(
        prediction_id=pid, candidate_id=f"cand-{pid}", decision_id="decision-x", user_id="admin",
        engine=engine, engine_version="2.2.0", source_type=source_type, source_name=source_name, source_version="1",
        symbol=symbol, timeframe=timeframe, direction=direction, confidence=0.7, target_horizon_seconds=horizon_min * 60,
        feature_snapshot_hash=f"hash-{pid}", generated_at=generated,
        resolution_deadline=generated + timedelta(minutes=horizon_min),
        reference_price=reference_price, target_reference_price=target, stop_reference_price=stop,
    )


def _candle(symbol, timeframe, ts_ms, close, high=None, low=None):
    return MarketCandle(symbol=symbol, timeframe=timeframe, timestamp=ts_ms, open=close, high=high or close,
                         low=low or close, close=close, volume=1.0, provider="binance_futures", quality_score=100.0)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _cleanup(db, *prediction_ids):
    db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(prediction_ids)).delete(synchronize_session=False)
    db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(prediction_ids)).delete(synchronize_session=False)
    db.commit()


def test_btc_resolves_from_local_candle_no_network(db):
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-local-1")
    db.add(row)
    db.flush()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("BTCUSDT", "5m", at_ms, close=101.0))
    db.commit()
    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["resolved"] == 1
    assert stats["primary_source"] == 1
    res = db.query(PredictionResolution).filter_by(prediction_id="btc-local-1").one()
    assert res.correct is True and res.resolved_direction == "LONG"
    _cleanup(db, "btc-local-1")


def test_eth_resolves_from_local_candle_no_network(db):
    row = _ledger(symbol="ETHUSDT", prediction_id="eth-local-1", direction="SHORT")
    db.add(row)
    db.flush()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("ETHUSDT", "5m", at_ms, close=95.0))
    db.commit()
    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["resolved"] == 1
    res = db.query(PredictionResolution).filter_by(prediction_id="eth-local-1").one()
    assert res.correct is True and res.resolved_direction == "SHORT"  # price fell, SHORT prediction correct
    _cleanup(db, "eth-local-1")


def test_binance_gap_falls_back_to_bybit(db, monkeypatch):
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-fallback-1")
    db.add(row)
    db.commit()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)

    async def fake_binance(symbol, timeframe, at, tf_ms):
        return providers.ResolutionPriceObservation("binance_futures", "binance", "usdt_perp", symbol, at, error="no_data")

    async def fake_bybit(symbol, timeframe, at, tf_ms):
        return providers.ResolutionPriceObservation("bybit", "bybit", "usdt_perp", symbol, at,
                                                      actual_market_timestamp=at, price=101.0, confidence=0.9)

    async def fake_dead(symbol, timeframe, at, tf_ms):
        return providers.ResolutionPriceObservation("x", "x", "x", symbol, at, error="timeout")

    monkeypatch.setattr(providers, "fetch_binance_futures", fake_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": fake_binance, "bybit": fake_bybit, "okx": fake_dead, "hyperliquid": fake_dead, "binance_spot": fake_dead})

    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["resolved"] == 1
    assert stats["fallback_source"] == 1
    res = db.query(PredictionResolution).filter_by(prediction_id="btc-fallback-1").one()
    assert res.fallback_used is True
    assert res.resolution_provider == "bybit"
    assert res.resolved_price == 101.0
    _cleanup(db, "btc-fallback-1")


def test_provider_disagreement_keeps_unresolved(db, monkeypatch):
    row = _ledger(symbol="ETHUSDT", prediction_id="eth-disagree-1")
    db.add(row)
    db.commit()

    async def fake_binance(symbol, timeframe, at, tf_ms):
        return providers.ResolutionPriceObservation("binance_futures", "binance", "usdt_perp", symbol, at, error="no_data")

    async def fake_bybit(symbol, timeframe, at, tf_ms):
        return providers.ResolutionPriceObservation("bybit", "bybit", "usdt_perp", symbol, at,
                                                      actual_market_timestamp=at, price=100.0, confidence=0.9)

    async def fake_okx(symbol, timeframe, at, tf_ms):
        # 5% away - way beyond the disagreement tolerance
        return providers.ResolutionPriceObservation("okx", "okx", "usdt_swap", symbol, at,
                                                      actual_market_timestamp=at, price=105.0, confidence=0.9)

    monkeypatch.setattr(providers, "fetch_binance_futures", fake_binance)
    monkeypatch.setattr(providers, "PROVIDER_FETCHERS", {"binance_futures": fake_binance, "bybit": fake_bybit, "okx": fake_okx, "hyperliquid": fake_okx, "binance_spot": fake_okx})

    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["resolved"] == 0
    assert stats["provider_disagreement"] == 1
    assert db.query(PredictionResolution).filter_by(prediction_id="eth-disagree-1").count() == 0
    row = db.query(PredictionLedger).filter_by(prediction_id="eth-disagree-1").one()
    assert row.unresolved_status == "exchange_price_disagreement"
    _cleanup(db, "eth-disagree-1")


def test_never_resolves_before_due_at(db):
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-notdue-1", minutes_ago=1, horizon_min=30)
    db.add(row)
    db.commit()
    stats = resolver.resolve_due_sync(db, limit=10)
    assert stats["scanned"] == 0  # not due yet - never even scanned
    assert db.query(PredictionResolution).filter_by(prediction_id="btc-notdue-1").count() == 0
    now = datetime.now(timezone.utc)
    assert resolver.classify_unresolved_reason(row, now) == "awaiting_horizon"
    _cleanup(db, "btc-notdue-1")


def test_resolution_is_idempotent(db):
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-idem-1")
    db.add(row)
    db.flush()
    at_ms = int(row.resolution_deadline.timestamp() * 1000)
    db.add(_candle("BTCUSDT", "5m", at_ms, close=101.0))
    db.commit()
    first = resolver.resolve_due_sync(db, limit=10)
    second = resolver.resolve_due_sync(db, limit=10)
    assert first["resolved"] == 1
    assert second["resolved"] == 0  # already resolved - not re-scanned
    assert db.query(PredictionResolution).filter_by(prediction_id="btc-idem-1").count() == 1
    _cleanup(db, "btc-idem-1")


def test_short_correct_and_long_wrong_classification(db):
    long_row = _ledger(symbol="BTCUSDT", prediction_id="btc-longwrong-1", direction="LONG", reference_price=100.0)
    short_row = _ledger(symbol="ETHUSDT", prediction_id="eth-shortcorrect-1", direction="SHORT", reference_price=100.0)
    db.add_all([long_row, short_row])
    db.flush()
    db.add(_candle("BTCUSDT", "5m", int(long_row.resolution_deadline.timestamp() * 1000), close=90.0))  # fell - LONG wrong
    db.add(_candle("ETHUSDT", "5m", int(short_row.resolution_deadline.timestamp() * 1000), close=90.0))  # fell - SHORT correct
    db.commit()
    resolver.resolve_due_sync(db, limit=10)
    long_res = db.query(PredictionResolution).filter_by(prediction_id="btc-longwrong-1").one()
    short_res = db.query(PredictionResolution).filter_by(prediction_id="eth-shortcorrect-1").one()
    assert long_res.correct is False
    assert short_res.correct is True
    _cleanup(db, "btc-longwrong-1", "eth-shortcorrect-1")


def test_neutral_classification_excluded_from_directional(db):
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-neutral-1", direction="LONG", reference_price=100.0)
    db.add(row)
    db.flush()
    db.add(_candle("BTCUSDT", "5m", int(row.resolution_deadline.timestamp() * 1000), close=100.0))  # flat -> return 0 -> NEUTRAL
    db.commit()
    resolver.resolve_due_sync(db, limit=10)
    res = db.query(PredictionResolution).filter_by(prediction_id="btc-neutral-1").one()
    assert res.neutral_result is True
    assert res.resolved_direction == "NEUTRAL"
    _cleanup(db, "btc-neutral-1")


def test_unsupported_timeframe_classified_not_fetched(db):
    row = _ledger(symbol="ETHUSDT", prediction_id="eth-1M-1", timeframe="1M", minutes_ago=60 * 24 * 40, horizon_min=60 * 24 * 35)
    now = datetime.now(timezone.utc)
    assert resolver.classify_unresolved_reason(row, now) == "unsupported_timeframe"


def test_outcome_status_never_green_or_red_when_unresolved():
    now = datetime.now(timezone.utc)
    future = _ledger(symbol="BTCUSDT", prediction_id="btc-status-1", minutes_ago=1, horizon_min=30)
    overdue = _ledger(symbol="BTCUSDT", prediction_id="btc-status-2", minutes_ago=30, horizon_min=5)
    overdue.resolver_attempts = 2
    assert resolver_status.outcome_status(future, None, now) == "unresolved_not_due"
    assert resolver_status.outcome_status(overdue, None, now) == "overdue_provider_error"


def test_symbol_map_scoped_symbols_never_hit_network(db, monkeypatch):
    """A symbol outside BTCUSDT/ETHUSDT must never trigger a provider fetch -
    this guards both scope and test-suite hermeticity (see resolver.py's
    _backfill_from_providers docstring)."""
    called = {"n": 0}

    async def spy(*a, **kw):
        called["n"] += 1
        return providers.ResolutionPriceObservation("x", "x", "x", "x", 0, error="should not be called")

    monkeypatch.setattr(providers, "fetch_binance_futures", spy)
    row = _ledger(symbol="SOLUSDT", prediction_id="sol-1")
    db.add(row)
    db.commit()
    resolver.resolve_due_sync(db, limit=10)
    assert called["n"] == 0
    row = db.query(PredictionLedger).filter_by(prediction_id="sol-1").one()
    assert row.unresolved_status == "unsupported_symbol"
    _cleanup(db, "sol-1")


def test_accuracy_summary_separates_btc_and_eth(db):
    assert symbol_map.supported("BTCUSDT") and symbol_map.supported("ETHUSDT")
    summary = resolver_status.accuracy_summary(db)
    assert set(summary["by_symbol"].keys()) <= {"BTCUSDT", "ETHUSDT"}
    assert summary["combined"] is not None


def test_latest_results_returns_at_most_limit(db):
    rows = resolver_status.latest_results(db, limit=10)
    assert len(rows) <= 10


def test_catchup_never_creates_a_trade(db):
    from app.db.models import Trade
    before = db.query(Trade).count()
    row = _ledger(symbol="BTCUSDT", prediction_id="btc-notrade-1")
    db.add(row)
    db.flush()
    db.add(_candle("BTCUSDT", "5m", int(row.resolution_deadline.timestamp() * 1000), close=101.0))
    db.commit()
    resolver.resolve_due_sync(db, limit=10)
    after = db.query(Trade).count()
    assert after == before
    _cleanup(db, "btc-notrade-1")


def test_resolver_module_never_imports_execution_path():
    import app.decision_engine.resolver as resolver_module
    with open(resolver_module.__file__) as f:
        content = f.read()
    assert "execution_router" not in content
    assert "BinanceExecutionProvider" not in content
