"""GET /api/dashboard's per-symbol external calls were sequential (Stage 1
performance audit measured ~1.7s for 6 total calls); now concurrent via
asyncio.gather. This proves the response shape/values are unaffected by
running them concurrently instead of sequentially."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.dashboard as dashboard_module


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
        symbol = params["symbol"]
        if "ticker" in url:
            return _FakeResponse({"symbol": symbol, "lastPrice": "100.0"})
        if "premiumIndex" in url:
            return _FakeResponse({"symbol": symbol, "lastFundingRate": "0.0001"})
        return _FakeResponse({"symbol": symbol, "openInterest": "12345"})


def make_client():
    app = FastAPI()
    app.include_router(dashboard_module.router)
    return TestClient(app)


def test_dashboard_returns_both_symbols_with_concurrent_fanout(monkeypatch):
    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = make_client()
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["symbols"]["BTCUSDT"]["ticker"]["symbol"] == "BTCUSDT"
    assert body["symbols"]["ETHUSDT"]["ticker"]["symbol"] == "ETHUSDT"
    assert body["symbols"]["BTCUSDT"]["funding"]["lastFundingRate"] == "0.0001"
    assert body["symbols"]["ETHUSDT"]["open_interest"]["openInterest"] == "12345"
