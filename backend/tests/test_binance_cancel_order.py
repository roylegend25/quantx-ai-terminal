"""Row-level "Cancel" on Real Open Orders (Debug 1.pdf section 1-4).

Two confirmed root causes, each covered here:

1. cancel_order/cancel_all_orders never invalidated the shared Binance
   snapshot cache, so the frontend's own immediate post-cancel refresh
   (milliseconds after the cancel resolves) served the pre-cancel cached
   orders list for up to BINANCE_ORDERS_TTL_SECONDS - a successful single
   cancel looked like it silently did nothing.
2. The cancel-order/cancel-all-orders endpoints required the bot's active
   trading mode to be exactly BINANCE_LIVE (_require_live), even though
   canceling a resting real order is a de-risking safety action that must
   work regardless of active mode - see ExecutionRouter._cancel_provider.
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
    """Endpoint-layer double: records what the router forwards without
    touching a real BinanceFuturesClient."""

    def __init__(self, ok=True, reason=None):
        self.ok = ok
        self.reason = reason
        self.calls: list[tuple] = []

    async def cancel_order(self, symbol, order_id, **kwargs):
        self.calls.append(("cancel_order", symbol, order_id))
        if not self.ok:
            return RouterResult(ok=False, mode=modes.MODE_LIVE, action="cancel_order", reason=self.reason)
        return RouterResult(ok=True, mode=modes.MODE_LIVE, action="cancel_order",
                            detail={"order": {"order_id": order_id, "status": "CANCELED"}})

    async def cancel_all_orders(self, symbol=None, **kwargs):
        self.calls.append(("cancel_all_orders", symbol))
        return RouterResult(ok=True, mode=modes.MODE_LIVE, action="cancel_all_orders",
                            detail={"canceled_symbols": ["BTCUSDT"]})


# ======================================================= provider-level ====

def test_cancel_order_classic_by_order_id_invalidates_snapshot():
    client = MockBinanceClient(open_orders=[make_order(order_id=101, type="LIMIT")])
    provider = make_provider(client)
    snapshot_service._orders.value = ["stale"]
    snapshot_service._orders.fetched_at = 9e18  # far future -> "fresh" until invalidated

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", order_id=101))

    assert result.ok, result.reason
    assert client.called("cancel_order")[0][1:] == ("BTCUSDT", 101)
    assert snapshot_service._orders.fetched_at == 0.0  # invalidated, not just overwritten


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

    async def fake_cancel_leg(c, symbol, algo_id, provider_name):
        client.calls.append(("cancel_leg", symbol, algo_id, provider_name))

    from app.trading import protection_provider
    monkeypatch.setattr(protection_provider, "cancel_leg", fake_cancel_leg)

    provider = make_provider(client)
    snapshot_service._orders.fetched_at = 9e18

    result = asyncio.run(provider.cancel_order(symbol="BTCUSDT", order_id=7788))

    assert result.ok, result.reason
    assert ("cancel_leg", "BTCUSDT", 7788, "algo") in client.calls
    assert snapshot_service._orders.fetched_at == 0.0


def test_failed_cancel_does_not_invalidate_snapshot():
    client = MockBinanceClient()

    async def failing_cancel(symbol, order_id):
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
    assert fake.calls == [("cancel_order", "BTCUSDT", 555)]


def test_router_cancel_all_orders_reaches_real_provider_while_locked(monkeypatch):
    fake = FakeCancelProvider()
    monkeypatch.setattr(execution_router, "_live", fake)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.set_mode(modes.MODE_LIVE)  # no unlock_live() -> effective mode degrades to LOCKED

    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    result = asyncio.run(execution_router.cancel_all_orders())

    assert result.ok, result.reason
    assert fake.calls == [("cancel_all_orders", None)]


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
    assert fake.calls == [("cancel_order", "BTCUSDT", 12345)]


def test_endpoint_cancel_all_orders_works_in_paper_mode(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-all-orders", json={"confirm": True})

    assert r.status_code == 200, r.text
    assert fake.calls == [("cancel_all_orders", None)]


def test_endpoint_cancel_order_missing_identifier_returns_400(monkeypatch):
    configure_keys(monkeypatch)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT"})

    assert r.status_code == 400
    assert "missing order id" in r.json()["detail"].lower()


def test_endpoint_cancel_order_requires_binance_configured():
    client = make_client()  # no keys configured
    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "order_id": 1})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"].lower()


def test_endpoint_cancel_order_uses_algo_id_when_order_id_absent(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-order", json={"symbol": "BTCUSDT", "algo_id": 9001})

    assert r.status_code == 200, r.text
    assert fake.calls == [("cancel_order", "BTCUSDT", 9001)]


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
    finally:
        db.close()


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


def test_endpoint_cancel_all_orders_still_requires_confirm(monkeypatch):
    configure_keys(monkeypatch)
    fake = FakeCancelProvider()
    monkeypatch.setattr(tc.execution_router, "_live", fake)
    modes.set_mode(modes.MODE_PAPER)
    client = make_client()

    r = client.post("/api/trading/binance/cancel-all-orders", json={})

    assert r.status_code == 400
    assert not fake.calls
