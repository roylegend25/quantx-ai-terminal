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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import prediction as prediction_module
from app.api.prediction import router as prediction_router
from app.db.models import PredictionFeature
from app.db.session import SessionLocal

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


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


async def _fake_evaluate_all_timeframes(symbol, market_context=None):
    return {"timeframes": {}, "consensus": None}


def make_client():
    app = FastAPI()
    app.include_router(prediction_router)
    return TestClient(app)


def _assert_valid_prediction_response(body: dict, symbol: str):
    assert body["symbol"] == symbol
    pred = body["prediction"]

    assert pred["direction"] in ("LONG", "SHORT", "NO_TRADE")
    assert 0.0 <= pred["confidence"] <= 100.0
    assert 0 <= pred["probability_up"] <= 100
    assert 0 <= pred["probability_down"] <= 100
    assert isinstance(pred["target"], (int, float))
    assert isinstance(pred["stop"], (int, float))
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
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)
    monkeypatch.setattr(prediction_module, "evaluate_all_timeframes", _fake_evaluate_all_timeframes)

    client = make_client()

    for symbol in SYMBOLS:
        prediction_module._prediction_cache.clear()
        resp = client.get(f"/api/prediction/{symbol}")
        assert resp.status_code == 200
        _assert_valid_prediction_response(resp.json(), symbol)


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
