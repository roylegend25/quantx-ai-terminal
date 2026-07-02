from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
from app.strategy import weighting


def make_client(monkeypatch, prices):
    prices_iter = iter(prices)

    async def fake_get_price(symbol: str) -> float:
        return next(prices_iter)

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)

    app = FastAPI()
    app.include_router(paper_module.router)
    return TestClient(app)


def test_closing_trade_adapts_strategy_weights(monkeypatch):
    # entry at 100, exit at 106 -> a winning LONG trade
    client = make_client(monkeypatch, prices=[100.0, 106.0])

    open_resp = client.post(
        "/api/paper/open",
        params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "sl": 98},
        json={
            "regime": "TRENDING",
            "strategies": {
                "trend": {"direction": "LONG", "confidence": 90},
                "momentum": {"direction": "LONG", "confidence": 70},
                "mean_reversion": {"direction": "SHORT", "confidence": 60},
                "breakout": {"direction": "NO_TRADE", "confidence": 0},
            },
        },
    )
    assert open_resp.status_code == 200
    trade_id = open_resp.json()["trade"]["id"]

    before = weighting.get_stats()
    assert before["trend"]["trade_count"] == 0

    close_resp = client.post(f"/api/paper/close/{trade_id}")
    assert close_resp.status_code == 200

    after = weighting.get_stats()

    # trend and momentum agreed with the LONG trade direction -> attributed
    assert after["trend"]["trade_count"] == 1
    assert after["momentum"]["trade_count"] == 1
    assert after["trend"]["win_rate"] == 100.0

    # entry 100, sl 98 -> risk_per_unit 2; exit 106 -> price_diff 6 -> r_multiple 3.0
    assert after["trend"]["avg_r_multiple"] == 3.0

    # mean_reversion disagreed (SHORT) and breakout had no opinion -> not attributed
    assert after["mean_reversion"]["trade_count"] == 0
    assert after["breakout"]["trade_count"] == 0

    # weights must have adapted away from the equal-weight cold start
    weights = weighting.get_weights()
    assert weights["trend"] > weighting.DEFAULT_WEIGHT
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_losing_trade_recorded_as_loss(monkeypatch):
    # entry at 100, exit at 97 -> a losing LONG trade
    client = make_client(monkeypatch, prices=[100.0, 97.0])

    open_resp = client.post(
        "/api/paper/open",
        params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "sl": 98},
        json={"regime": "RANGING", "strategies": {"trend": {"direction": "LONG", "confidence": 80}}},
    )
    trade_id = open_resp.json()["trade"]["id"]

    client.post(f"/api/paper/close/{trade_id}")

    stats = weighting.get_stats()["trend"]
    assert stats["trade_count"] == 1
    assert stats["win_rate"] == 0.0
    assert stats["avg_r_multiple"] < 0
