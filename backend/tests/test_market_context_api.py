from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.market import router as market_router
from app.intelligence import hyperliquid_trades, market_intelligence


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


def test_hyperliquid_large_trades_route_registered_before_symbol_catch_all(monkeypatch):
    client = make_client()

    async def fake_fetch(coins, min_notional):
        return {"trades": [], "coins": list(coins), "min_notional": min_notional, "data_source": "hyperliquid_ws"}

    monkeypatch.setattr(hyperliquid_trades, "fetch", fake_fetch)

    resp = client.get("/api/market/hyperliquid/large-trades")
    assert resp.status_code == 200
    body = resp.json()
    # must hit the dedicated hyperliquid handler, not /{symbol} with symbol="hyperliquid"
    assert body["coins"] == ["BTC", "ETH"]
    assert "ticker" not in body


def test_hyperliquid_large_trades_route_filters_unsupported_coins_and_passes_min_notional(monkeypatch):
    client = make_client()
    captured = {}

    async def fake_fetch(coins, min_notional):
        captured["coins"] = coins
        captured["min_notional"] = min_notional
        return {"trades": [], "coins": list(coins), "min_notional": min_notional, "data_source": "hyperliquid_ws"}

    monkeypatch.setattr(hyperliquid_trades, "fetch", fake_fetch)

    resp = client.get("/api/market/hyperliquid/large-trades?coins=BTC,DOGE&min_notional=250000")
    assert resp.status_code == 200
    assert captured["coins"] == ("BTC",)  # DOGE isn't a supported coin, silently dropped rather than erroring
    assert captured["min_notional"] == 250000.0


def test_hyperliquid_large_trades_route_never_accepts_a_negative_threshold(monkeypatch):
    client = make_client()
    captured = {}

    async def fake_fetch(coins, min_notional):
        captured["min_notional"] = min_notional
        return {"trades": [], "coins": list(coins), "min_notional": min_notional, "data_source": "hyperliquid_ws"}

    monkeypatch.setattr(hyperliquid_trades, "fetch", fake_fetch)

    resp = client.get("/api/market/hyperliquid/large-trades?min_notional=-500")
    assert resp.status_code == 200
    assert captured["min_notional"] == 0.0
