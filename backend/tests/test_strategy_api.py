from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategy import router as strategy_router
from app.strategy.performance_repository import repository


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
        assert "current_weight" in stats
        assert "trades" in stats
        assert "wins" in stats
        assert "losses" in stats
        assert "rolling_win_rate" in stats
        assert "average_r_multiple" in stats
        assert "profit_factor" in stats
        assert "sharpe_ratio" in stats
        assert "max_drawdown" in stats
        assert "average_confidence" in stats

    total_weight = sum(s["current_weight"] for s in body.values())
    assert abs(total_weight - 1.0) < 1e-6


def test_weights_endpoint_reflects_recorded_trades():
    repository.update_metrics(
        "trend", r_multiple=3.0, win=True, confidence=90, regime="TRENDING"
    )

    client = make_client()
    body = client.get("/api/strategy/weights").json()

    assert body["trend"]["trades"] == 1
    assert body["trend"]["rolling_win_rate"] == 100.0
