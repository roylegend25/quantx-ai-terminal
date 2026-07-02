from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
from app.api.strategy import router as strategy_router
from app.strategy.performance_repository import DEFAULT_WEIGHT
from app.strategy.performance_repository import repository as performance_repository
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository


def test_recording_shadow_metrics_does_not_move_production_weights():
    for _ in range(10):
        rolling_metrics_repository.record_trade(
            "trend", r_multiple=5.0, win=True, confidence=99, regime="TRENDING"
        )

    # the shadow table reflects the strong performance...
    assert rolling_metrics_repository.get("trend")["trades"] == 10
    assert rolling_metrics_repository.get("trend")["rolling_win_rate"] == 100.0

    # ...but the production weight ensemble.py consumes is untouched
    weights = performance_repository.get_weights()
    assert weights["trend"] == DEFAULT_WEIGHT
    assert performance_repository.get("trend")["trades"] == 0


def test_weights_endpoint_unaffected_by_shadow_metrics(monkeypatch):
    for _ in range(5):
        rolling_metrics_repository.record_trade(
            "momentum", r_multiple=-3.0, win=False, confidence=10, regime="HIGH_VOL"
        )

    app = FastAPI()
    app.include_router(strategy_router)
    client = TestClient(app)

    body = client.get("/api/strategy/weights").json()

    # GET /api/strategy/weights stays backward compatible: same shape,
    # same equal-weight cold start, no leakage from the shadow table
    assert set(body.keys()) == {"trend", "momentum", "mean_reversion", "breakout"}
    assert body["momentum"]["current_weight"] == DEFAULT_WEIGHT
    assert body["momentum"]["trades"] == 0
    assert abs(sum(s["current_weight"] for s in body.values()) - 1.0) < 1e-6


def test_closing_trade_updates_both_repositories_independently(monkeypatch):
    prices_iter = iter([100.0, 106.0])

    async def fake_get_price(symbol: str) -> float:
        return next(prices_iter)

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)

    app = FastAPI()
    app.include_router(paper_module.router)
    client = TestClient(app)

    open_resp = client.post(
        "/api/paper/open",
        params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "sl": 98},
        json={
            "regime": "TRENDING",
            "strategies": {"trend": {"direction": "LONG", "confidence": 90}},
        },
    )
    trade_id = open_resp.json()["trade"]["id"]

    weights_before = performance_repository.get_weights()

    client.post(f"/api/paper/close/{trade_id}")

    # both tables recorded the same closed trade...
    assert performance_repository.get("trend")["trades"] == 1
    assert rolling_metrics_repository.get("trend")["trades"] == 1

    # ...and the production weight still moved exactly as it did before
    # this shadow table existed (unchanged behavior, same call site)
    weights_after = performance_repository.get_weights()
    assert weights_after["trend"] > weights_before["trend"]

    # the shadow candidate weight is also live now, computed independently
    shadow_weights = rolling_metrics_repository.get_all()
    assert abs(sum(s["current_weight"] for s in shadow_weights.values()) - 1.0) < 1e-6
    assert shadow_weights["trend"]["current_weight"] > DEFAULT_WEIGHT
