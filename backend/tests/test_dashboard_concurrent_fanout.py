"""GET /api/dashboard's per-symbol external calls were sequential (Stage 1
performance audit measured ~1.7s for 6 total calls); now concurrent via
asyncio.gather. This proves the response shape/values are unaffected by
running them concurrently instead of sequentially.

Stage 2 performance fix: the AsyncClient is now a module-level pooled
singleton (was created fresh per request) and symbol snapshots are cached
for a couple of seconds with single-flight dedup. Every test resets that
module state first, so a monkeypatched fake client from one test can never
leak into another via the pooled client or a still-warm cache entry."""
import asyncio

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
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def get(self, url, params=None, **kwargs):
        type(self).call_count += 1
        symbol = params["symbol"]
        if "ticker" in url:
            return _FakeResponse({"symbol": symbol, "lastPrice": "100.0"})
        if "premiumIndex" in url:
            return _FakeResponse({"symbol": symbol, "lastFundingRate": "0.0001"})
        return _FakeResponse({"symbol": symbol, "openInterest": "12345"})


@pytest.fixture(autouse=True)
def _reset_dashboard_module():
    dashboard_module._reset_for_tests()
    yield
    dashboard_module._reset_for_tests()


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


def test_dashboard_pools_client_and_caches_within_ttl(monkeypatch):
    """A second poll within the TTL window must not make fresh Binance calls -
    this is the Stage 2 fix for /api/dashboard duplicating the WS loop's own
    per-second fetch of the same ticker/funding/OI data."""
    _FakeAsyncClient.call_count = 0
    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = make_client()

    r1 = client.get("/api/dashboard")
    assert r1.status_code == 200
    calls_after_first = _FakeAsyncClient.call_count
    assert calls_after_first == 6  # 3 calls x 2 symbols

    r2 = client.get("/api/dashboard")
    assert r2.status_code == 200
    assert _FakeAsyncClient.call_count == calls_after_first  # served from cache, no new calls
    assert r2.json() == r1.json()


def test_symbol_snapshot_single_flight_dedup(monkeypatch):
    """Concurrent callers within the TTL window share one in-flight fetch
    instead of each starting their own."""
    _FakeAsyncClient.call_count = 0
    monkeypatch.setattr(dashboard_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = dashboard_module._http_client()

    async def run():
        return await asyncio.gather(
            dashboard_module.symbol_snapshot(client, "BTCUSDT"),
            dashboard_module.symbol_snapshot(client, "BTCUSDT"),
            dashboard_module.symbol_snapshot(client, "BTCUSDT"),
        )

    results = asyncio.run(run())
    assert _FakeAsyncClient.call_count == 3  # one fetch (3 endpoints) for all 3 callers
    assert all(r == results[0] for r in results)
