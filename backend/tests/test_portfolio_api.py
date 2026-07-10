"""/api/portfolio in paper and (mocked) Binance testnet mode, plus the
ownership rule that one user cannot touch another user's position."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
import app.api.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.db.models import ExchangePositionRow, Trade, TradingControl
from app.exchanges.binance_models import (
    BinanceAccountSummary,
    BinanceBalance,
    BinanceOrder,
    BinancePosition,
    BinanceUserTrade,
)
from app.trading import modes
from app.trading.execution_router import router as execution_router


@pytest.fixture(autouse=True)
def paper_mode():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.commit()
    finally:
        db.close()
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client(monkeypatch, price=50000.0):
    async def fake_get_price(symbol: str) -> float:
        return price

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)
    app = FastAPI()
    app.include_router(portfolio_module.router)
    return TestClient(app)


def open_paper_trade(**kw):
    db = SessionLocal()
    try:
        defaults = dict(symbol="BTCUSDT", side="LONG", entry=50000.0, qty=0.02,
                        status="OPEN", pnl=0.0, leverage=1.0, margin_used=1000.0)
        defaults.update(kw)
        trade = Trade(**defaults)
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return trade.id
    finally:
        db.close()


# ------------------------------------------------------------- paper mode

def test_summary_returns_paper_data_in_paper_mode(monkeypatch):
    open_paper_trade()
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "PAPER"
    assert data["open_positions"] == 1
    assert data["total_wallet_balance"] == 10000.0
    assert data["risk"]["kill_switch_active"] is False
    assert data["risk"]["live_enabled"] is False


def test_balances_positions_trades_paper_mode(monkeypatch):
    open_paper_trade()
    client = make_client(monkeypatch)

    balances = client.get("/api/portfolio/balances").json()
    assert balances["mode"] == "PAPER"
    assert balances["balances"][0]["asset"] == "USDT"

    positions = client.get("/api/portfolio/positions").json()
    assert positions["mode"] == "PAPER"
    assert len(positions["positions"]) == 1
    assert positions["positions"][0]["symbol"] == "BTCUSDT"

    orders = client.get("/api/portfolio/orders").json()
    assert orders == {"mode": "PAPER", "orders": []}

    trades = client.get("/api/portfolio/trades").json()
    assert trades["mode"] == "PAPER"
    assert len(trades["trades"]) == 1


# ----------------------------------------------------------- testnet mode

class MockPortfolioClient:
    configured = True

    async def get_account_info(self):
        return BinanceAccountSummary(
            total_wallet_balance=500.0, available_balance=400.0, total_margin_balance=505.0,
            total_unrealized_pnl=5.0, total_initial_margin=50.0, total_maintenance_margin=1.0,
        )

    async def get_positions(self, symbol=None):
        return [BinancePosition(
            symbol="ETHUSDT", side="SHORT", quantity=0.05, entry_price=3000.0, mark_price=2990.0,
            leverage=2.0, margin_type="isolated", liquidation_price=4400.0,
            unrealized_pnl=0.5, margin_used=75.0, notional=149.5,
        )]

    async def get_open_orders(self, symbol=None):
        return [BinanceOrder(
            order_id=42, client_order_id="qxsl-1", symbol="ETHUSDT", side="BUY", position_side="BOTH",
            type="STOP_MARKET", status="NEW", price=0.0, stop_price=3100.0, quantity=0.05,
            executed_qty=0.0, avg_price=0.0, reduce_only=True, close_position=False,
        )]

    async def get_daily_realized_pnl(self):
        return -3.5

    async def get_balances(self):
        return [BinanceBalance(asset="USDT", balance=500.0, available=400.0)]

    async def get_trade_history(self, symbol, limit=50):
        if symbol != "ETHUSDT":
            return []
        return [BinanceUserTrade(
            trade_id=7, order_id=42, symbol="ETHUSDT", side="SELL", price=3000.0,
            quantity=0.05, realized_pnl=1.25, commission=0.02, commission_asset="USDT", time=1,
        )]


def use_mock_testnet_provider(monkeypatch):
    modes.set_mode(modes.MODE_TESTNET)
    monkeypatch.setattr(execution_router._testnet, "_client", MockPortfolioClient())


def test_summary_returns_binance_data_in_testnet_mode(monkeypatch):
    use_mock_testnet_provider(monkeypatch)
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "BINANCE_TESTNET"
    assert data["total_wallet_balance"] == 500.0
    assert data["daily_pnl"] == -3.5
    assert data["open_positions"] == 1
    assert data["total_notional_exposure"] == 149.5


def test_positions_orders_trades_in_testnet_mode(monkeypatch):
    use_mock_testnet_provider(monkeypatch)
    client = make_client(monkeypatch)

    positions = client.get("/api/portfolio/positions").json()
    assert positions["mode"] == "BINANCE_TESTNET"
    pos = positions["positions"][0]
    assert pos["symbol"] == "ETHUSDT"
    assert pos["sl"] == 3100.0  # from the resting STOP_MARKET order
    assert pos["liquidation_price"] == 4400.0

    orders = client.get("/api/portfolio/orders").json()
    assert orders["orders"][0]["order_id"] == 42
    assert orders["orders"][0]["reduce_only"] is True

    trades = client.get("/api/portfolio/trades").json()
    assert trades["trades"][0]["realized_pnl"] == 1.25


# --------------------------------------------------------------- ownership

def test_user_cannot_touch_another_users_position(monkeypatch):
    trade_id = open_paper_trade(user_id="alice")

    async def fake_get_price(symbol: str) -> float:
        return 50000.0

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)
    app = FastAPI()
    app.include_router(paper_module.router)
    app.dependency_overrides[paper_module.get_optional_user] = lambda: "mallory"
    client = TestClient(app)

    edit = client.patch(f"/api/paper/positions/{trade_id}/risk", json={"stop_loss": 49000.0})
    assert edit.status_code == 403

    close = client.post(f"/api/paper/positions/{trade_id}/close", json={})
    assert close.status_code == 403
