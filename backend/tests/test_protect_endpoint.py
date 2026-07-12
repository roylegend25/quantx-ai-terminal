"""POST /api/trading/binance/positions/{symbol}/protect - the immediate
repair path for an existing real Binance position with no (or stale) TP/SL.
Uses the same MockBinanceClient double as test_execution_router_binance.py."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.trading_control as tc
from app.db.session import SessionLocal
from app.db.models import BinanceProtectionCapability, TradingControl
from app.trading import modes, real_risk_gate
from tests.test_execution_router_binance import MockBinanceClient, btc_long_position, make_provider


def short_position(symbol="ETHUSDT"):
    from app.exchanges.binance_models import BinancePosition
    return BinancePosition(
        symbol=symbol, side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.30,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(BinanceProtectionCapability).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def install_client(monkeypatch, client):
    monkeypatch.setattr(tc.execution_router, "_testnet", make_provider(client))
    return client


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def test_protect_returns_404_when_no_position_exists(monkeypatch):
    install_client(monkeypatch, MockBinanceClient(position=None))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r.status_code == 404


def test_protect_validates_short_take_profit_direction(monkeypatch):
    """SHORT: take_profit must be below mark/entry."""
    install_client(monkeypatch, MockBinanceClient(position=short_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1850.0, "stop_loss": 1810.0})  # TP above mark - invalid for SHORT
    assert r.status_code == 400
    assert "take profit" in r.json()["detail"].lower()


def test_protect_validates_short_stop_loss_direction(monkeypatch):
    """SHORT: stop_loss must be above mark/entry."""
    install_client(monkeypatch, MockBinanceClient(position=short_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1750.0})  # SL below mark - invalid for SHORT
    assert r.status_code == 400
    assert "stop loss" in r.json()["detail"].lower()


def test_protect_validates_long_direction(monkeypatch):
    install_client(monkeypatch, MockBinanceClient(position=btc_long_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/BTCUSDT/protect",
                    json={"take_profit": 40000.0, "stop_loss": 60000.0})  # swapped - invalid for LONG
    assert r.status_code == 400


def test_protect_places_tp_and_sl_and_confirms_protection(monkeypatch):
    install_client(monkeypatch, MockBinanceClient(position=short_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r.status_code == 200
    body = r.json()
    assert body["protection_status"] == "PROTECTED"
    assert body["live_take_profit"] == 1792.0
    assert body["live_stop_loss"] == 1810.0
    assert body["errors"] == []


def test_protect_matches_the_exact_reported_incident(monkeypatch):
    """The real production scenario this endpoint was built for: ETHUSDT
    SHORT, 0.038 @ 1800.49, no TP/SL."""
    install_client(monkeypatch, MockBinanceClient(position=short_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0, "mode": "full_position"})
    assert r.status_code == 200
    assert r.json()["protection_status"] == "PROTECTED"


def test_protect_cancels_stale_protective_orders_first(monkeypatch):
    from tests.test_execution_router_binance import make_order
    stale = make_order(order_id=999, symbol="ETHUSDT", type="STOP_MARKET", stop_price=1830.0, reduce_only=True)
    mock_client = MockBinanceClient(position=short_position(), open_orders=[stale])
    install_client(monkeypatch, mock_client)
    client = make_client()

    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r.status_code == 200
    assert mock_client.called("cancel_order")[0][2] == 999
    # the fresh SL replaces the stale one - only one STOP_MARKET remains
    sl_orders = [o for o in mock_client.open_orders if o.type == "STOP_MARKET"]
    assert len(sl_orders) == 1
    assert sl_orders[0].order_id != 999


def test_protect_handles_binance_rejection_safely(monkeypatch):
    mock_client = MockBinanceClient(position=short_position())

    async def fail_tp(symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        from app.exchanges.binance_errors import BinanceOrderRejected
        raise BinanceOrderRejected("Order type not supported for this endpoint.", code=-1116)

    mock_client.place_take_profit = fail_tp
    install_client(monkeypatch, mock_client)
    client = make_client()

    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r.status_code == 200  # the request itself succeeded; protection did not
    body = r.json()
    assert body["protection_status"] != "PROTECTED"
    assert any("take_profit" in e for e in body["errors"])


def test_protect_no_secrets_in_response(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", "AKIAFAKEKEY1234567890")
    monkeypatch.setattr(settings, "binance_api_secret", "supersecretvalue0987654321")
    install_client(monkeypatch, MockBinanceClient(position=short_position()))
    client = make_client()
    r = client.post("/api/trading/binance/positions/ETHUSDT/protect",
                    json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert "AKIAFAKEKEY1234567890" not in r.text
    assert "supersecretvalue0987654321" not in r.text
