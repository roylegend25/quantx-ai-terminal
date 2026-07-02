from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategy import router as strategy_router
from app.strategy import weighting


def make_client():
    app = FastAPI()
    app.include_router(strategy_router)
    return TestClient(app)


def test_weights_endpoint_shape():
    client = make_client()
    resp = client.get("/api/strategy/weights")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"trend", "momentum", "mean_reversion", "breakout"}

    for name, stats in body.items():
        assert "weight" in stats
        assert "win_rate" in stats
        assert "avg_r_multiple" in stats
        assert "profit_factor" in stats
        assert "sharpe_ratio" in stats
        assert "max_drawdown" in stats
        assert "avg_confidence" in stats
        assert "regime_performance" in stats

    total_weight = sum(s["weight"] for s in body.values())
    assert abs(total_weight - 1.0) < 1e-6


def test_weights_endpoint_reflects_recorded_trades():
    weighting.record_trade_result(
        "trend", r_multiple=3.0, win=True, confidence=90, regime="TRENDING"
    )

    client = make_client()
    body = client.get("/api/strategy/weights").json()

    assert body["trend"]["trade_count"] == 1
    assert body["trend"]["win_rate"] == 100.0
