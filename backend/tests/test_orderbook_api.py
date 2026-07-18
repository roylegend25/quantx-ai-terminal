from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.orderbook import router as orderbook_router


def make_client():
    app = FastAPI()
    app.include_router(orderbook_router)
    return TestClient(app)


def _fake_response(payload, status=200):
    resp = MagicMock()
    resp.json.return_value = payload
    if status == 200:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock(status_code=status))
    return resp


def test_orderbook_response_includes_normalized_market_metadata():
    payload = {
        "T": 1700000000000, "E": 1700000000000,
        "bids": [["100.0", "1.0"]], "asks": [["100.5", "2.0"]],
    }
    with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload))):
        client = make_client()
        r = client.get("/api/orderbook/BTCUSDT")

    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "binance_futures"
    assert body["source_type"] == "exchange_rest"
    assert body["market_timestamp"] == 1700000000.0
    assert body["stale"] is False
    assert body["error"] is None
    assert body["latency_ms"] is not None
    assert body["bids"] == [{"price": 100.0, "qty": 1.0}]


def test_orderbook_reports_a_clean_error_instead_of_a_raw_500_on_upstream_failure():
    with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response({}, status=502))):
        client = make_client()
        r = client.get("/api/orderbook/BTCUSDT")

    assert r.status_code == 502
    assert "Could not reach Binance order book" in r.json()["detail"]
