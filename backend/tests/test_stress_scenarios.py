"""Regression coverage for the System Stress Test fixes.

Each test here pins down one of the five behaviors that used to make
app/stress/simulator.py report a FAILED scenario:
  - missing_candles crashed (regime.detect() couldn't compare None to a float)
  - extreme_spread was never vetoed (no spread awareness in risk_manager)
  - duplicate_prediction_cycle could open past max_open_positions (no atomic
    check at the trade-write layer)
  - position_manager_delayed had no protection independent of the dedicated
    poll loop's own cadence
  - zero_volume_candle was never vetoed (no volume awareness anywhere)

Plus one end-to-end test that runs the real stress suite and asserts all 15
scenarios pass, so a future regression in any of these shows up here instead
of only in the dashboard.
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
from app.db.models import Trade
from app.db.session import SessionLocal
from app.quant.indicators import compute_features
from app.risk import settings_repository
from app.strategy.regime import detect
from app.trading import risk_manager


# ---------------------------------------------------------------------------
# missing_candles: regime.detect() must not crash on a present-but-None value
# ---------------------------------------------------------------------------

def test_regime_detect_handles_none_bb_width_and_volatility():
    # bb_width/realized_volatility are present (compute_features() always
    # sets the key) but None until their rolling window is satisfied - the
    # bug was features.get(key, 0) not substituting a default for this case.
    regime = detect({"trend_score": 1, "realized_volatility": None, "bb_width": None})
    assert regime in {"TRENDING", "RANGING", "HIGH_VOL", "NORMAL"}


def test_compute_features_does_not_crash_on_five_candles():
    candles = []
    price = 50_000.0
    for i in range(5):
        candles.append({
            "time": 1_700_000_000_000 + i * 300_000,
            "open": price, "high": price * 1.001, "low": price * 0.999,
            "close": price, "volume": 10.0,
        })
    features = compute_features(candles)["symbol_features"]
    assert features["candle_count"] == 5
    # RSI/ATR/Bollinger genuinely can't be computed from 5 candles - that's
    # expected and fine, as long as it's None, not a crash.
    assert features["rsi"] is None
    assert features["atr"] is None


# ---------------------------------------------------------------------------
# missing_candles / stale_price_data: make_prediction() overrides to NO_TRADE
# ---------------------------------------------------------------------------

def test_make_prediction_blocks_insufficient_candle_history():
    from app.api.prediction import make_prediction, MIN_CANDLES_FOR_PREDICTION

    features = {
        "price": 100.0, "ema20": 95.0, "ema50": 90.0, "ema200": 85.0,
        "rsi": 65.0, "macd_hist": 1.0, "atr": 2.0, "bb_width": 0.03,
        "realized_volatility": 0.01, "trend_score": 3, "regime": "bullish_trend",
        "candle_count": MIN_CANDLES_FOR_PREDICTION - 1,
        "volume": 10.0, "volume_sma20": 10.0, "stale": False,
    }
    result = make_prediction(features)
    assert result["direction"] == "NO_TRADE"
    assert result["risk"]["allowed"] is False
    assert "Insufficient candle history" in result["risk"]["reason"]


def test_make_prediction_blocks_stale_feed():
    from app.api.prediction import make_prediction

    features = {
        "price": 100.0, "ema20": 95.0, "ema50": 90.0, "ema200": 85.0,
        "rsi": 65.0, "macd_hist": 1.0, "atr": 2.0, "bb_width": 0.03,
        "realized_volatility": 0.01, "trend_score": 3, "regime": "bullish_trend",
        "candle_count": 220, "volume": 10.0, "volume_sma20": 10.0,
        "stale": True, "candle_age_seconds": 3600,
    }
    result = make_prediction(features)
    assert result["direction"] == "NO_TRADE"
    assert result["risk"]["allowed"] is False
    assert "stale" in result["risk"]["reason"].lower()


def test_make_prediction_unaffected_when_candle_count_key_absent():
    # A hand-built features dict (unit tests exercising the ensemble
    # directly) that never populates candle_count/stale must not be treated
    # as "insufficient" by default - only the real pipeline, which always
    # sets these keys, triggers the guard.
    from app.api.prediction import make_prediction

    features = {
        "price": 100.0, "ema20": 95.0, "ema50": 90.0, "ema200": 85.0,
        "rsi": 65.0, "macd_hist": 1.0, "atr": 2.0, "bb_width": 0.03,
        "realized_volatility": 0.01, "trend_score": 3, "regime": "bullish_trend",
    }
    result = make_prediction(features)
    assert result["risk"]["reason"] != "Insufficient candle history (None < 50) for a reliable prediction"


# ---------------------------------------------------------------------------
# extreme_spread / zero_volume_candle: risk_manager vetoes on real, pure checks
# ---------------------------------------------------------------------------

def test_spread_allowed_vetoes_wide_spread_and_passes_tight_spread():
    ok, reason = risk_manager.spread_allowed(5.1)
    assert ok is False
    assert "spread" in reason.lower()

    ok, reason = risk_manager.spread_allowed(0.05)
    assert ok is True

    # unknown (no live order book) fails open, not closed
    ok, reason = risk_manager.spread_allowed(None)
    assert ok is True


def test_volume_allowed_vetoes_zero_and_illiquid_volume():
    ok, reason = risk_manager.volume_allowed(0.0, 1_000.0)
    assert ok is False

    ok, reason = risk_manager.volume_allowed(10.0, 1_000.0)  # 1% of average
    assert ok is False

    ok, reason = risk_manager.volume_allowed(900.0, 1_000.0)  # 90% of average
    assert ok is True

    # unknown (no candle data) fails open, not closed
    ok, reason = risk_manager.volume_allowed(None, None)
    assert ok is True


def test_evaluate_risk_blocks_on_spread_and_volume():
    portfolio = {"balance": 10_000.0, "daily_pnl": 0.0}

    wide_spread = risk_manager.evaluate_risk(
        confidence=90.0, direction="LONG", open_positions=0,
        portfolio=portfolio, trade_history=[], spread_pct=5.0,
    )
    assert wide_spread["allowed"] is False
    assert "spread" in wide_spread["reason"].lower()

    halted_volume = risk_manager.evaluate_risk(
        confidence=90.0, direction="LONG", open_positions=0,
        portfolio=portfolio, trade_history=[], volume=0.0, volume_sma20=1_000.0,
    )
    assert halted_volume["allowed"] is False
    assert "volume" in halted_volume["reason"].lower()


# ---------------------------------------------------------------------------
# duplicate_prediction_cycle: POST /api/paper/open enforces max_open_positions
# ---------------------------------------------------------------------------

def _paper_client(monkeypatch, prices):
    prices_iter = iter(prices)

    async def fake_get_price(symbol: str) -> float:
        return next(prices_iter)

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)

    app = FastAPI()
    app.include_router(paper_module.router)
    from app.core.security import create_internal_service_token
    return TestClient(app, headers={"Authorization":f"Bearer {create_internal_service_token()}"})


def test_open_trade_rejects_past_max_open_positions(monkeypatch):
    settings_repository.reset_settings()
    settings_repository.update_settings({"max_open_positions": 1})

    client = _paper_client(monkeypatch, prices=[100.0, 100.0])

    first = client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000})
    assert first.status_code == 200

    second = client.post("/api/paper/open", params={"symbol": "ETHUSDT", "side": "LONG", "usdt_size": 1000})
    assert second.status_code == 400
    assert "maximum open positions" in second.json()["detail"].lower()


def test_open_trade_allows_up_to_the_configured_limit(monkeypatch):
    settings_repository.reset_settings()
    settings_repository.update_settings({"max_open_positions": 2})

    client = _paper_client(monkeypatch, prices=[100.0, 100.0, 100.0])

    for symbol in ("BTCUSDT", "ETHUSDT"):
        resp = client.post("/api/paper/open", params={"symbol": symbol, "side": "LONG", "usdt_size": 1000})
        assert resp.status_code == 200

    third = client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "SHORT", "usdt_size": 1000})
    assert third.status_code == 400

    settings_repository.reset_settings()


# ---------------------------------------------------------------------------
# position_manager_delayed: GET /positions closes a breached stop inline
# ---------------------------------------------------------------------------

def test_get_positions_closes_breached_stop_loss_inline(monkeypatch):
    db = SessionLocal()
    try:
        trade = Trade(symbol="BTCUSDT", side="LONG", entry=100.0, qty=1.0, status="OPEN", sl=95.0, tp=110.0)
        db.add(trade)
        db.commit()
        db.refresh(trade)
        trade_id = trade.id
    finally:
        db.close()

    # Mark price has already gapped below the stop-loss - if only the
    # dedicated position-manager loop enforced this, a stalled loop would
    # leave the position open indefinitely. GET /positions must catch it too.
    async def fake_get_price(symbol: str) -> float:
        return 90.0

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)

    app = FastAPI()
    app.include_router(paper_module.router)
    client = TestClient(app)

    resp = client.get("/api/paper/positions")
    assert resp.status_code == 200
    assert all(p["id"] != trade_id for p in resp.json()["positions"])

    db = SessionLocal()
    try:
        closed = db.get(Trade, trade_id)
        assert closed.status == "CLOSED"
        assert closed.exit == 90.0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# end-to-end: the real stress suite reports 15/15 PASSED
# ---------------------------------------------------------------------------

def test_full_stress_suite_passes():
    from app.stress.simulator import run_all

    db = SessionLocal()
    try:
        results = asyncio.run(run_all(db))
    finally:
        db.close()

    failed = [r for r in results if r["status"] != "PASSED"]
    assert not failed, [(r["scenario_id"], r["checks"]) for r in failed]
    assert len(results) == 15
