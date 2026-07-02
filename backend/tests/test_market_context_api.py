from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.market import router as market_router
from app.intelligence import market_intelligence


def make_client():
    app = FastAPI()
    app.include_router(market_router)
    return TestClient(app)


def test_context_route_registered_before_symbol_catch_all(monkeypatch):
    client = make_client()

    async def fake_get_context(symbol, force_refresh=False):
        return {"symbol": symbol, "market_bias": "NEUTRAL", "bias_score": 0.0}

    monkeypatch.setattr(market_intelligence, "get_context", fake_get_context)

    resp = client.get("/api/market/context")
    assert resp.status_code == 200
    body = resp.json()
    # must hit the dedicated /context handler, not /{symbol} with symbol="context"
    assert "market_bias" in body
    assert "ticker" not in body


def test_context_route_accepts_symbol_query_param(monkeypatch):
    client = make_client()

    async def fake_get_context(symbol, force_refresh=False):
        return {"symbol": symbol, "market_bias": "BULLISH"}

    monkeypatch.setattr(market_intelligence, "get_context", fake_get_context)

    resp = client.get("/api/market/context?symbol=ETHUSDT")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "ETHUSDT"
