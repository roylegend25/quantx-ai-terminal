"""Locks in that GET /api/prediction/{symbol} and GET /api/prediction/history
return the same well-formed shape for BTCUSDT and ETHUSDT.

make_prediction()/ensemble.evaluate() never receive a `symbol` argument at
all (see app/api/prediction.py, app/strategy/ensemble.py) - direction and
confidence are computed purely from OHLCV-derived features. These tests
exercise the actual route for both symbols against identical synthetic
market data, so a regression that special-cased (or broke) one symbol's
wiring - route registration, klines fetch, feature computation, response
shape - would show up here even though the two feed the same pipeline.
"""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import prediction as prediction_module
from app.api.prediction import router as prediction_router
from app.db.models import PredictionFeature
from app.db.session import SessionLocal

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAMES = ["30m", "1h", "4h"]


def _synthetic_klines(n=220, start_price=100.0, step=0.05):
    """A gentle, steady uptrend - shape only, no symbol-specific magnitude
    baked in, since the real bug surface here is pipeline wiring, not
    signal quality. Anchored to "now" so the staleness check passes."""
    import time

    price = start_price
    last_time = int(time.time() * 1000)
    t0 = last_time - (n - 1) * 300_000
    rows = []
    for i in range(n):
        price += step
        o, h, l, c, v = price - 0.02, price + 0.03, price - 0.05, price, 100.0 + i
        rows.append([t0 + i * 300_000, str(o), str(h), str(l), str(c), str(v)])
    return rows


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, **kwargs):
        return _FakeResponse(_synthetic_klines())


async def _fake_get_context(symbol, force_refresh=False):
    return None


def make_client():
    app = FastAPI()
    app.include_router(prediction_router)
    return TestClient(app)


def _assert_valid_prediction_response(body: dict, symbol: str, timeframe: str = "5m"):
    assert body["symbol"] == symbol
    assert body["timeframe"] == timeframe
    assert body["interval"] == timeframe
    assert isinstance(body["reason"], str) and body["reason"]
    horizon = body["prediction_horizon"]
    assert horizon["interval"] == timeframe
    assert horizon["bars"] > 0
    assert horizon["horizon_ms"] > 0

    pred = body["prediction"]

    assert pred["direction"] in ("LONG", "SHORT", "NO_TRADE")
    if pred["direction"] == "NO_TRADE":
        assert pred["confidence"] is None
        assert pred["decision_engine"]["directional_confidence"] is None
        assert pred["target"] is None
        assert pred["stop"] is None
    else:
        assert 0.0 <= pred["confidence"] <= 100.0
        assert isinstance(pred["target"], (int, float))
        assert isinstance(pred["stop"], (int, float))
    assert 0 <= pred["probability_up"] <= 100
    assert 0 <= pred["probability_down"] <= 100
    assert isinstance(pred["price"], (int, float))
    assert pred["regime"]

    assert set(pred["strategies"].keys()) == {
        "trend", "momentum", "mean_reversion", "breakout",
    }
    for result in pred["strategies"].values():
        assert result["direction"] in ("LONG", "SHORT", "NO_TRADE")

    risk = pred["risk"]
    assert isinstance(risk["allowed"], bool)
    assert isinstance(risk["reason"], str) and risk["reason"]
    assert isinstance(risk["required_confidence"], (int, float))

    features = pred["features"]
    assert features["candle_count"] == 220
    assert features["stale"] is False


def test_prediction_route_returns_valid_structure_for_both_symbols(monkeypatch):
    """compute_and_persist_prediction (the scheduler-facing entry point,
    called by trading_engine.py and the observation loop - see
    app.trading.scheduler) is exercised directly here rather than through
    GET /{symbol}, since that route is read-only (main-purpose
    consolidation, Stage 2) and must never itself trigger computation."""
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)

    for symbol in SYMBOLS:
        prediction_module._prediction_cache.clear()
        body = asyncio.run(prediction_module.compute_and_persist_prediction(symbol, current_user="multi-symbol-user"))
        _assert_valid_prediction_response(body, symbol)


def test_prediction_route_returns_valid_structure_for_every_timeframe(monkeypatch):
    """30m/1h/4h must return either a real directional prediction or an
    honest NO_TRADE with a reason - never a malformed/empty response - for
    both BTCUSDT and ETHUSDT."""
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            prediction_module._prediction_cache.clear()
            body = asyncio.run(prediction_module.compute_and_persist_prediction(
                symbol, interval=tf, current_user="multi-symbol-user",
            ))
            _assert_valid_prediction_response(body, symbol, timeframe=tf)


def test_prediction_route_accepts_timeframe_query_param_as_interval_alias(monkeypatch):
    """?timeframe=1h must actually compute on 1h data instead of silently
    falling back to the "5m" default - previously `timeframe` was not a
    recognized parameter at all and was dropped without error."""
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)
    prediction_module._prediction_cache.clear()

    body = asyncio.run(prediction_module.compute_and_persist_prediction(
        "BTCUSDT", timeframe="1h", current_user="multi-symbol-user",
    ))
    assert body["interval"] == "1h"
    assert body["timeframe"] == "1h"


def test_prediction_route_supports_3m_timeframe(monkeypatch):
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)
    body = asyncio.run(prediction_module.compute_and_persist_prediction(
        "BTCUSDT", timeframe="3m", current_user="multi-symbol-user",
    ))
    assert body["timeframe"] == "3m"


def test_get_route_is_read_only_and_returns_the_persisted_decision(monkeypatch):
    """GET /{symbol} must never compute - it only reads back whatever
    compute_and_persist_prediction most recently persisted (main-purpose
    consolidation, Stage 2)."""
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)
    prediction_module._prediction_cache.clear()

    client = make_client()

    # Nothing computed yet for this fresh (symbol, timeframe, user) triple -
    # GET must return an honest "awaiting first computation" state, not a
    # fabricated decision and not a 500.
    resp = client.get("/api/prediction/BTCUSDT", params={"timeframe": "2h"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "awaiting_first_computation"
    assert resp.json()["direction"] is None

    computed = asyncio.run(prediction_module.compute_and_persist_prediction(
        "BTCUSDT", timeframe="2h", current_user=prediction_module.settings.admin_username,
    ))

    compute_calls = {"count": 0}
    original = prediction_module._compute_and_persist_prediction

    async def counting_compute(*args, **kwargs):
        compute_calls["count"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(prediction_module, "_compute_and_persist_prediction", counting_compute)

    resp = client.get("/api/prediction/BTCUSDT", params={"timeframe": "2h"})
    assert resp.status_code == 200
    assert compute_calls["count"] == 0, "GET must never call the compute path"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["prediction"]["decision_id"] == computed["prediction"]["decision_id"]


def test_prediction_history_route_returns_a_list_for_both_symbols():
    db = SessionLocal()
    try:
        db.query(PredictionFeature).delete()
        db.commit()
        for symbol in SYMBOLS:
            db.add(PredictionFeature(
                symbol=symbol, timeframe="5m", direction="LONG", confidence=80.0,
                entry_price=100.0, target=105.0, stop=97.0,
            ))
        db.commit()

        client = make_client()
        for symbol in SYMBOLS:
            resp = client.get(f"/api/prediction/history?symbol={symbol}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["symbol"] == symbol
            assert isinstance(body["history"], list)
            assert len(body["history"]) == 1
            assert body["history"][0]["symbol"] == symbol
    finally:
        db.query(PredictionFeature).delete()
        db.commit()
        db.close()


def test_prediction_history_filters_by_symbol_and_timeframe_together():
    """A BTC 1h row must never leak into an ETH query or a 15m query -
    never show ETH history on a BTC chart, never show 15m history on a 1h
    chart."""
    db = SessionLocal()
    try:
        db.query(PredictionFeature).delete()
        db.commit()
        combos = [
            ("BTCUSDT", "1h"), ("BTCUSDT", "15m"), ("BTCUSDT", "30m"), ("BTCUSDT", "4h"),
            ("ETHUSDT", "1h"), ("ETHUSDT", "30m"), ("ETHUSDT", "4h"),
        ]
        for symbol, tf in combos:
            db.add(PredictionFeature(
                symbol=symbol, timeframe=tf, direction="LONG", confidence=80.0,
                entry_price=100.0, target=105.0, stop=97.0,
            ))
        db.commit()

        client = make_client()
        for symbol, tf in combos:
            resp = client.get("/api/prediction/history", params={"symbol": symbol, "timeframe": tf})
            assert resp.status_code == 200
            body = resp.json()
            assert body["count"] == 1, f"expected exactly one row for {symbol}@{tf}, got {body['count']}"
            row = body["history"][0]
            assert row["symbol"] == symbol
            assert row["timeframe"] == tf
            # the enriched aliases required by the history-record contract
            assert "actual_price_at_prediction" in row
            assert "actual_price_when_resolved" in row
            assert "error_pct" in row
    finally:
        db.query(PredictionFeature).delete()
        db.commit()
        db.close()
