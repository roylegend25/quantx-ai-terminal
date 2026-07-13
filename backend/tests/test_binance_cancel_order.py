"""Row-level "Cancel" on Real Open Orders (Debug 1.pdf: single Cancel not
working while Cancel All works).

Confirmed root causes, each covered here:

1. cancel_order/cancel_all_orders never invalidated the shared Binance
   snapshot cache, so the frontend's own immediate post-cancel refresh
   (milliseconds after the cancel resolves) served the pre-cancel cached
   orders list for up to BINANCE_ORDERS_TTL_SECONDS - a successful single
   cancel looked like it silently did nothing.
2. The cancel-order/cancel-all-orders endpoints required the bot's active
   trading mode to be exactly BINANCE_LIVE (_require_live), even though
   canceling a resting real order is a de-risking safety action that must
   work regardless of active mode - see ExecutionRouter._cancel_provider.
3. Only orderId/algoId were ever resolvable - a row carrying only a
   client_order_id/client_algo_id (or an unrecognized `provider`) had no
   safe path to cancel at all.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingAuditLog, TradingControl
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes
from app.trading.execution_router import RouterResult, router as execution_router
from tests.test_execution_router_binance import MockBinanceClient, make_order, make_provider

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(TradingAuditLog).delete()
        db.commit()
    finally:
        db.close()
    snapshot_service.reset()
    execution_router._live = None
    yield
    modes.set_mode(modes.MODE_PAPER)
    execution_router._live = None


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def configure_keys(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)


class FakeCancelProvider:
    """Endpoint/router-layer double: records the exact identifier set
    forwarded to it, without touching a real BinanceFuturesClient."""

    def __init__(self, ok=True, reason=None):
        self.ok = ok
        self.reason = reason
        self.calls: list[dict] = []

    async def cancel_order(self, symbol, order_id=None, client_order_id=None,
                           algo_id=None, client_algo_id=None, provider=None, **kwargs):
        self.calls.append({
            "action": "cancel_order", "symbol": symbol, "order_id": order_id,
            "client_order_id": client_order_id, "algo_id": algo_id,
            "client_algo_id": client_algo_id, "provider": provider,
        })
        if not self.ok:
            return RouterResult(ok=False, mode=modes.MODE_LIVE, action="cancel_order", reason=self.reason)
        return RouterResult(ok=True, mode=modes.MODE_LIVE, action="cancel_order",
                            detail={"order": {"order_id": order_id, "status": "CANCELED"}})

    async def cancel_all_orders(self, symbol=None, **kwargs):
        self.calls.append({"action": "cancel_all_orders", "symbol": symbol})
        return RouterResult(ok=True, mode=modes.MODE_LIVE, action="cancel_all_orders",
                            detail={"canceled_symbols": ["BTCUSDT"]})


# ======================================================= provider-level ====
# BinanceExecutionProvider.cancel_order against a mocked BinanceFuturesClient
# - no network, exercises the real classic/algo routing + cache invalidation.

def test_cancel_order_classic_by_order_id_invalidates_snapshot():
    client = MockBinanceClient(open_orders=[make_order(order_id=101, type="LIMIT")])
    provider = make_provider(client)
    snapshot_service._orders.value = ["stale"]
    snapshot_service._orders.fetched_at = 9e18  # far future -> "fresh" until invalidated

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", order_id=101))

    assert result.ok, result.reason
    assert client.called("cancel_order")[0] == ("cancel_order", "BTCUSDT", 101, None)
    assert snapshot_service._orders.fetched_at == 0.0  # invalidated, not just overwritten


def test_cancel_order_classic_by_client_order_id_only():
    """A row that only ever carries a clientOrderId (no numeric orderId)
    must still be cancelable - origClientOrderId is Binance's own
    documented alternative identifier for DELETE /fapi/v1/order."""
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", client_order_id="qx-abc123"))

    assert result.ok, result.reason
    assert client.called("cancel_order")[0] == ("cancel_order", "BTCUSDT", None, "qx-abc123")


def test_cancel_order_algo_by_tracked_algo_id_invalidates_snapshot(monkeypatch):
    from app.db.models import ExchangePositionRow

    db = SessionLocal()
    try:
        db.add(ExchangePositionRow(
            mode=modes.MODE_TESTNET, symbol="BTCUSDT", side="LONG", quantity=0.01,
            entry_price=50000, mark_price=50000, tp_algo_id=None, sl_algo_id=7788,
        ))
        db.commit()
    finally:
        db.close()

    client = MockBinanceClient()

    async def fake_cancel_leg(c, symbol, algo_id, provider_name, client_algo_id=None, client_order_id=None):
        client.calls.append(("cancel_leg", symbol, algo_id, provider_name, client_algo_id))

    from app.trading import protection_provider
    monkeypatch.setattr(protection_provider, "cancel_leg", fake_cancel_leg)

    provider = make_provider(client)
    snapshot_service._orders.fetched_at = 9e18

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", order_id=7788))

    assert result.ok, result.reason
    assert ("cancel_leg", "BTCUSDT", 7788, "algo", None) in client.calls
    assert snapshot_service._orders.fetched_at == 0.0


def test_cancel_order_algo_by_explicit_provider_and_algo_id(monkeypatch):
    """A row that already carries provider="algo" + algo_id routes directly
    to the algo surface - no DB lookup / tracked-id inference needed."""
    client = MockBinanceClient()

    async def fake_cancel_leg(c, symbol, algo_id, provider_name, client_algo_id=None, client_order_id=None):
        client.calls.append(("cancel_leg", symbol, algo_id, provider_name, client_algo_id))

    from app.trading import protection_provider
    monkeypatch.setattr(protection_provider, "cancel_leg", fake_cancel_leg)
    provider = make_provider(client)

    result = asyncio.run(provider.cancel_order(symbol="ETHUSDT", algo_id=8899, provider="algo"))

    assert result.ok, result.reason
    assert ("cancel_leg", "ETHUSDT", 8899, "algo", None) in client.calls
    assert not client.called("cancel_order")  # never touches the classic endpoint


def test_cancel_order_algo_by_client_algo_id_only(monkeypatch):
    client = MockBinanceClient()

    async def fake_cancel_leg(c, symbol, algo_id, provider_name, client_algo_id=None, client_order_id=None):
        client.calls.append(("cancel_leg", symbol, algo_id, provider_name, client_algo_id))

    from app.trading import protection_provider
    monkeypatch.setattr(protection_provider, "cancel_leg", fake_cancel_leg)
    provider = make_provider(client)

    result = asyncio.run(provider.cancel_order(symbol="ETHUSDT", client_algo_id="qxa-xyz", provider="algo"))

    assert result.ok, result.reason
    assert ("cancel_leg", "ETHUSDT", None, "algo", "qxa-xyz") in client.calls


def test_failed_cancel_does_not_invalidate_snapshot():
    client = MockBinanceClient()

    async def failing_cancel(symbol, order_id=None, orig_client_order_id=None):
        raise RuntimeError("Unknown order sent.")

    client.cancel_order = failing_cancel
    provider = make_provider(client)
    snapshot_service._orders.fetched_at = 9e18

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", order_id=999))

    assert not result.ok
    assert snapshot_service._orders.fetched_at == 9e18  # untouched - nothing changed on Binance


# ==================================================== router mode-independence

def test_router_cancel_order_reaches_real_provider_while_mode_is_paper(monkeypatch):
    fake = FakeCancelProvider()
    monkeypatch.setattr(execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)

    result = asyncio.run(execution_router.cancel_order(symbol="BTCUSDT", order_id=555))

    assert result.ok, result.reason
    assert fake.calls == [{"action": "cancel_order", "symbol": "BTCUSDT", "order_id": 555,
                           "client_order_id": None, "algo_id": None, "client_algo_id": None, "provider": None}]


def test_router_cancel_all_orders_reaches_real_provider_while_locked(monkeypatch):
    fake = FakeCancelProvider()
    monkeypatch.setattr(execution_router, "_live", fake)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.set_mode(modes.MODE_LIVE)  # no unlock_live() -> effective mode degrades to LOCKED

    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    result = asyncio.run(execution_router.cancel_all_orders())

    assert result.ok, result.reason
    assert fake.calls == [{"action": "cancel_all_orders", "symbol": None}]


def test_router_cancel_order_fails_closed_when_server_lock_off(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    configure_keys(monkeypatch)
    modes.set_mode(modes.MODE_PAPER)

    result = asyncio.run(execution_router.cancel_order(symbol="BTCUSDT", order_id=1))

    assert not result.ok
    assert "BINANCE_LIVE_ENABLED" in (result.reason or "")


# =========================================================== endpoint-level

def test_endpoint_cancel_order_works_in_paper_mode(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 12345})

    assert r.status_code == 200, r.text
    assert fake.calls[0]["order_id"] == 12345
    assert fake.calls[0]["symbol"] == "BTCUSDT"


def test_endpoint_cancel_order_classic_by_client_order_id(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "BTCUSDT", "provider": "classic", "client_order_id": "qx-abc"})

    assert r.status_code == 200, r.text
    assert fake.calls[0]["client_order_id"] == "qx-abc"
    assert fake.calls[0]["order_id"] is None
    assert fake.calls[0]["provider"] == "classic"


def test_endpoint_cancel_order_uses_algo_id_when_order_id_absent(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "algo_id": 9001})

    assert r.status_code == 200, r.text
    assert fake.calls[0]["algo_id"] == 9001
    assert fake.calls[0]["order_id"] is None


def test_endpoint_cancel_order_algo_by_client_algo_id(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "ETHUSDT", "provider": "algo", "client_algo_id": "qxa-999"})

    assert r.status_code == 200, r.text
    assert fake.calls[0]["client_algo_id"] == "qxa-999"
    assert fake.calls[0]["algo_id"] is None
    assert fake.calls[0]["provider"] == "algo"


def test_endpoint_cancel_order_missing_identifier_returns_400(monkeypatch):
    configure_keys(monkeypatch)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT"})

    assert r.status_code == 400
    assert "missing order identifier" in r.json()["detail"].lower()


def test_endpoint_cancel_order_missing_symbol_returns_400(monkeypatch):
    configure_keys(monkeypatch)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"order_id": 1})

    assert r.status_code == 400
    assert "missing symbol" in r.json()["detail"].lower()


def test_endpoint_cancel_order_invalid_provider_returns_400(monkeypatch):
    configure_keys(monkeypatch)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "BTCUSDT", "order_id": 1, "provider": "iceberg"})

    assert r.status_code == 400
    assert "invalid provider" in r.json()["detail"].lower()


def test_endpoint_cancel_order_provider_classic_requires_classic_identifier(monkeypatch):
    """provider="classic" with only an algo_id present must not silently
    cancel the wrong surface - it's a 400, not a guess."""
    configure_keys(monkeypatch)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "BTCUSDT", "provider": "classic", "algo_id": 1})

    assert r.status_code == 400
    assert "missing order identifier" in r.json()["detail"].lower()


def test_endpoint_cancel_order_requires_binance_configured():
    client = make_client()  # no keys configured
    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 1})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"].lower()


def test_endpoint_cancel_order_writes_audit_log(monkeypatch):
    configure_keys(monkeypatch)
    client_double = MockBinanceClient(open_orders=[make_order(order_id=42, type="LIMIT")])
    provider = make_provider(client_double)
    provider.mode = modes.MODE_LIVE
    monkeypatch.setattr(tc.execution_router, "_live", provider)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 42})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        events = db.query(TradingAuditLog).filter(TradingAuditLog.event == "order_canceled").all()
        assert len(events) == 1
        assert events[0].detail["order_id"] == 42
        assert events[0].detail["provider"] == "classic"
    finally:
        db.close()


def test_endpoint_cancel_order_invalidates_snapshot(monkeypatch):
    configure_keys(monkeypatch)
    client_double = MockBinanceClient(open_orders=[make_order(order_id=77, type="LIMIT")])
    provider = make_provider(client_double)
    provider.mode = modes.MODE_LIVE
    monkeypatch.setattr(tc.execution_router, "_live", provider)
    modes.set_mode(modes.MODE_PAPER)
    snapshot_service._orders.fetched_at = 9e18
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 77})

    assert r.status_code == 200, r.text
    assert snapshot_service._orders.fetched_at == 0.0


def test_endpoint_cancel_order_never_exposes_secrets(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider(ok=False, reason="Binance rejected the cancel: Unknown order sent.")
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 1})

    assert r.status_code == 400
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    assert "signature" not in r.text.lower()


def test_endpoint_cancel_all_orders_works_in_paper_mode(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-all-orders", json={"confirm": True})

    assert r.status_code == 200, r.text
    assert fake.calls == [{"action": "cancel_all_orders", "symbol": None}]


def test_endpoint_cancel_all_orders_still_requires_confirm(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-all-orders", json={})

    assert r.status_code == 400
    assert not fake.calls


# ============================================== exchange-client parameter shape
#
# Debug 1.pdf section 4/5: verifies the actual parameter NAMES sent to
# Binance for both cancel surfaces, independent of the router/endpoint
# layers above (no mocking of cancel_order/cancel itself - only the raw
# signed _delete transport, so a regression in param-name construction
# can't hide behind a higher-level mock).

def test_classic_client_sends_order_id_param():
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    captured = {}

    async def fake_delete(path, params=None, priority=None):
        captured["path"] = path
        captured["params"] = params
        return {"symbol": "BTCUSDT", "orderId": 555, "status": "CANCELED"}

    client._delete = fake_delete
    asyncio.run(client.cancel_order("btcusdt", order_id=555))

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"] == {"symbol": "BTCUSDT", "orderId": 555}
    assert "origClientOrderId" not in captured["params"]


def test_classic_client_sends_orig_client_order_id_param():
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    captured = {}

    async def fake_delete(path, params=None, priority=None):
        captured["path"] = path
        captured["params"] = params
        return {"symbol": "BTCUSDT", "orderId": 0, "clientOrderId": "qx-abc", "status": "CANCELED"}

    client._delete = fake_delete
    asyncio.run(client.cancel_order("btcusdt", orig_client_order_id="qx-abc"))

    assert captured["params"] == {"symbol": "BTCUSDT", "origClientOrderId": "qx-abc"}
    assert "orderId" not in captured["params"]


def test_classic_client_cancel_requires_an_identifier():
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    with pytest.raises(ValueError):
        asyncio.run(client.cancel_order("BTCUSDT"))


def test_algo_provider_sends_algo_id_param():
    from app.exchanges.binance_algo_provider import BinanceAlgoProvider
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    captured = {}

    async def fake_delete(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"algoId": 8899, "code": "200"}

    client._delete = fake_delete
    asyncio.run(BinanceAlgoProvider(client).cancel(algo_id=8899))

    assert captured["path"] == "/fapi/v1/algoOrder"
    assert captured["params"] == {"algoId": 8899}
    assert "clientAlgoId" not in captured["params"]
    assert "orderId" not in captured["params"]


def test_algo_provider_sends_client_algo_id_param():
    from app.exchanges.binance_algo_provider import BinanceAlgoProvider
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    captured = {}

    async def fake_delete(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"clientAlgoId": "qxa-999", "code": "200"}

    client._delete = fake_delete
    asyncio.run(BinanceAlgoProvider(client).cancel(client_algo_id="qxa-999"))

    assert captured["params"] == {"clientAlgoId": "qxa-999"}
    assert "algoId" not in captured["params"]


def test_algo_provider_cancel_requires_an_identifier():
    from app.exchanges.binance_algo_provider import BinanceAlgoProvider
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    client = BinanceFuturesClient(api_key="k", api_secret="s", testnet=True)
    with pytest.raises(ValueError):
        asyncio.run(BinanceAlgoProvider(client).cancel())


# ==================================== live-verified: large order-id precision
#
# Root cause of "row Cancel still fails, Cancel All works" (confirmed live
# against the real account 2026-07-13): Binance futures order ids on this
# account run up to 19 digits (e.g. 8389766233056201670), past
# Number.MAX_SAFE_INTEGER (2^53-1, 16 digits). Serializing order_id as a
# raw JSON number let a browser's own JSON.parse silently round it to the
# nearest representable double BEFORE any frontend code ran, so row Cancel
# always sent the WRONG id back and Binance correctly rejected it as
# unknown - while Cancel All (DELETE /fapi/v1/allOpenOrders, no order_id
# involved at all) was never affected. Fixed by serializing these ids as
# JSON strings end to end (BinanceOrder.to_dict()); Pydantic already
# coerces a numeric string back to Python's arbitrary-precision int
# losslessly, so no request-model change was needed on the way in.

BIG_ORDER_ID = 8389766233056201670  # exceeds 2**53 - 1


def test_order_row_serializes_big_order_id_as_string():
    order = make_order(order_id=BIG_ORDER_ID, type="LIMIT")
    d = order.to_dict()
    assert d["order_id"] == str(BIG_ORDER_ID)
    assert isinstance(d["order_id"], str)


def test_cancel_order_endpoint_accepts_big_order_id_as_json_string(monkeypatch):
    """Proves the fix end-to-end at the API boundary: a request body
    carrying order_id as a JSON *string* (exactly what a browser now sends,
    per api.ts) reaches the classic Binance client with the value fully
    intact - no float round-trip, no truncation."""
    configure_keys(monkeypatch)
    captured = {}

    class CapturingProvider:
        async def cancel_order(self, symbol, order_id=None, **kwargs):
            captured["order_id"] = order_id
            captured["type"] = type(order_id).__name__
            return RouterResult(ok=True, mode=modes.MODE_LIVE, action="cancel_order",
                                detail={"order": {"order_id": str(order_id), "status": "CANCELED"}})

    monkeypatch.setattr(tc.execution_router, "_live", CapturingProvider())
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "ETHUSDT", "provider": "classic", "order_id": str(BIG_ORDER_ID)})

    assert r.status_code == 200, r.text
    assert captured["order_id"] == BIG_ORDER_ID  # exact, no precision loss
    assert captured["type"] == "int"  # Pydantic coerced the string to Python's arbitrary-precision int


def test_position_protection_order_ids_serialize_as_strings(monkeypatch):
    """GET /binance/positions' sl_order_id/tp_order_id feed the same Cancel
    SL/TP buttons as the orders table's row Cancel - same precision risk,
    same fix required."""
    from tests.test_portfolio_api import MockReadClient, make_client, use_mock_read_client

    use_mock_read_client(monkeypatch, MockReadClient())
    client = make_client(monkeypatch)

    positions = client.get("/api/portfolio/binance/positions").json()["positions"]
    assert positions[0]["sl_order_id"] == "42"
    assert isinstance(positions[0]["sl_order_id"], str)


# ============================================================ debug endpoint

def test_debug_endpoint_returns_safe_shape(monkeypatch):
    configure_keys(monkeypatch)
    client_double = MockBinanceClient(open_orders=[make_order(order_id=BIG_ORDER_ID, type="LIMIT")])
    monkeypatch.setattr("app.api.portfolio._read_client", client_double)
    client = make_client()

    r = client.get("/api/trading/binance/open-orders/debug")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orders"] == [{
        "symbol": "BTCUSDT", "type": "LIMIT", "provider": "classic",
        "has_order_id": True, "has_client_order_id": True,
        "has_algo_id": False, "has_client_algo_id": False,
        "cancel_payload_preview": {
            "symbol": "BTCUSDT", "provider": "classic",
            "order_id_present": True, "client_order_id_present": True,
            "algo_id_present": False, "client_algo_id_present": False,
        },
    }]


def test_debug_endpoint_never_exposes_secrets(monkeypatch):
    configure_keys(monkeypatch)
    client_double = MockBinanceClient(open_orders=[make_order(order_id=BIG_ORDER_ID, type="LIMIT")])
    monkeypatch.setattr("app.api.portfolio._read_client", client_double)
    client = make_client()

    r = client.get("/api/trading/binance/open-orders/debug")

    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    assert "signature" not in r.text.lower()


# ===================================== endpoint-level classic vs algo routing

def test_endpoint_classic_row_reaches_classic_client_not_algo(monkeypatch):
    """A row shaped exactly like the screenshot in Debug 1 (ETHUSDT BUY
    LIMIT, provider=classic, order_id set) must reach
    BinanceFuturesClient.cancel_order specifically - never the Algo Order
    API - end to end through the real endpoint + router + provider chain
    (only the raw Binance HTTP client is mocked)."""
    configure_keys(monkeypatch)
    client_double = MockBinanceClient(open_orders=[make_order(order_id=BIG_ORDER_ID, symbol="ETHUSDT", type="LIMIT")])
    provider = make_provider(client_double)
    monkeypatch.setattr(tc.execution_router, "_live", provider)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order",
                    json={"symbol": "ETHUSDT", "provider": "classic", "order_id": str(BIG_ORDER_ID)})

    assert r.status_code == 200, r.text
    assert client_double.called("cancel_order")[0] == ("cancel_order", "ETHUSDT", BIG_ORDER_ID, None)
    # never touches the Algo Order API surface for a classic row
    assert not any(c[0] in ("_delete", "cancel_leg") for c in client_double.calls)
