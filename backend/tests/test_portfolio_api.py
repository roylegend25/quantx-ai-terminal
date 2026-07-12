"""Phase 23: /api/portfolio/paper/* and /api/portfolio/binance/* are two
strictly separate surfaces - paper always available, Binance read through a
read-only production client with safe error classification - plus the
ownership rule that one user cannot touch another user's paper position."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
import app.api.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.db.models import ExchangePositionRow, Trade, TradingControl
from app.exchanges.binance_errors import BinancePermissionError, BinanceRateLimitError
from app.exchanges.binance_models import (
    BinanceAccountSummary,
    BinanceBalance,
    BinanceOrder,
    BinancePosition,
    BinanceUserTrade,
)
from app.trading import modes


FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client(monkeypatch, price=50000.0):
    async def fake_get_price(symbol: str) -> float:
        return price

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)
    app = FastAPI()
    app.include_router(portfolio_module.router)
    return TestClient(app)


def configure_keys(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)


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


class MockReadClient:
    """Healthy production account double for the read-only client."""

    configured = True
    read_only = True

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

    async def get_income_history(self, limit=50, income_type=None):
        return [{"symbol": "ETHUSDT", "income_type": "FUNDING_FEE", "income": -0.01,
                 "asset": "USDT", "info": "", "time": 1}]


def use_mock_read_client(monkeypatch, client=None):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", client or MockReadClient())


# ------------------------------------------------------------- paper space

def test_paper_summary_is_paper_data_regardless_of_mode(monkeypatch):
    open_paper_trade()
    use_mock_read_client(monkeypatch)
    modes.set_mode(modes.MODE_LIVE)  # active mode must not leak into the paper view
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/paper/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "PAPER"
    assert data["balance"] == 10000.0
    assert data["open_positions"] == 1
    assert data["risk"]["kill_switch_active"] is False


def test_paper_positions_orders_trades(monkeypatch):
    open_paper_trade()
    client = make_client(monkeypatch)

    positions = client.get("/api/portfolio/paper/positions").json()
    assert positions["mode"] == "PAPER"
    assert len(positions["positions"]) == 1

    orders = client.get("/api/portfolio/paper/orders").json()
    assert orders == {"mode": "PAPER", "orders": []}

    trades = client.get("/api/portfolio/paper/trades").json()
    assert trades["mode"] == "PAPER"
    assert len(trades["trades"]) == 1


# ----------------------------------------------------------- binance space

def test_binance_summary_uses_env_key_but_never_returns_it(monkeypatch):
    use_mock_read_client(monkeypatch)
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/binance/summary")
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    data = r.json()
    assert data["available"] is True
    assert data["total_wallet_balance"] == 500.0
    assert data["daily_pnl"] == -3.5
    assert data["open_positions"] == 1


def test_binance_and_paper_portfolios_are_separate(monkeypatch):
    open_paper_trade()  # BTCUSDT paper LONG
    use_mock_read_client(monkeypatch)  # ETHUSDT real SHORT
    client = make_client(monkeypatch)

    paper = client.get("/api/portfolio/paper/positions").json()["positions"]
    real = client.get("/api/portfolio/binance/positions").json()["positions"]

    assert [p["symbol"] for p in paper] == ["BTCUSDT"]
    assert [p["symbol"] for p in real] == ["ETHUSDT"]
    # the real view carries live TP/SL from resting reduce-only orders
    assert real[0]["sl"] == 3100.0
    assert real[0]["liquidation_price"] == 4400.0
    # MockReadClient's position has a resting SL but no TP
    assert real[0]["protection_status"] == "MISSING_TP"
    assert real[0]["tp"] is None


def test_binance_positions_ignore_non_reduce_only_orders_for_protection(monkeypatch):
    """A resting order that happens to share symbol/type but isn't
    reduce-only/closePosition must never be counted as protection - Phase
    27's whole point is that protection status is verified, not assumed."""
    from app.exchanges.binance_models import BinanceOrder

    class NoProtectionClient(MockReadClient):
        async def get_open_orders(self, symbol=None):
            return [BinanceOrder(
                order_id=99, client_order_id="manual-1", symbol="ETHUSDT", side="SELL", position_side="BOTH",
                type="STOP_MARKET", status="NEW", price=0.0, stop_price=3100.0, quantity=0.05,
                executed_qty=0.0, avg_price=0.0, reduce_only=False, close_position=False,
            )]

    use_mock_read_client(monkeypatch, client=NoProtectionClient())
    client = make_client(monkeypatch)
    real = client.get("/api/portfolio/binance/positions").json()["positions"]
    assert real[0]["protection_status"] == "MISSING_TP_SL"
    assert real[0]["sl"] is None


def test_binance_not_configured_returns_safe_unavailable(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/binance/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["reason"] == "Binance API key not configured"


def test_binance_permission_error_returns_safe_warning(monkeypatch):
    class PermissionDeniedClient(MockReadClient):
        async def get_account_info(self):
            raise BinancePermissionError("Invalid API-key, IP, or permissions for action.", code=-2015)

    use_mock_read_client(monkeypatch, PermissionDeniedClient())
    client = make_client(monkeypatch)

    r = client.get("/api/portfolio/binance/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert "IP whitelist" in data["reason"]
    # the raw exchange message (or anything else) never leaks
    assert "-2015" not in r.text


def test_binance_rate_limit_returns_safe_warning(monkeypatch):
    class RateLimitedClient(MockReadClient):
        async def get_balances(self):
            raise BinanceRateLimitError("Too many requests", status=429)

    use_mock_read_client(monkeypatch, RateLimitedClient())
    client = make_client(monkeypatch)
    data = client.get("/api/portfolio/binance/balances").json()
    assert data["available"] is False
    assert "Rate limited" in data["reason"]


def test_binance_orders_trades_income(monkeypatch):
    use_mock_read_client(monkeypatch)
    client = make_client(monkeypatch)

    orders = client.get("/api/portfolio/binance/orders").json()
    assert orders["orders"][0]["order_id"] == 42
    assert orders["orders"][0]["reduce_only"] is True

    trades = client.get("/api/portfolio/binance/trades").json()
    assert trades["trades"][0]["realized_pnl"] == 1.25
    # order 42 was never journaled as a bot trade -> synced label
    assert trades["trades"][0]["label"] == "SYNCED_FROM_BINANCE"

    income = client.get("/api/portfolio/binance/income").json()
    assert income["income"][0]["income_type"] == "FUNDING_FEE"


# --------------------------------------------------- account snapshot cache

def test_account_snapshot_is_cached_across_rapid_calls(monkeypatch):
    """The Live Margin Calculator (2s poll) and other cards can call
    binance_summary back-to-back without each one hitting Binance."""
    call_count = {"account_info": 0}

    class CountingClient(MockReadClient):
        async def get_account_info(self):
            call_count["account_info"] += 1
            return await super().get_account_info()

    use_mock_read_client(monkeypatch, client=CountingClient())
    client = make_client(monkeypatch)

    r1 = client.get("/api/portfolio/binance/summary").json()
    r2 = client.get("/api/portfolio/binance/summary").json()

    assert r1["total_wallet_balance"] == r2["total_wallet_balance"] == 500.0
    assert call_count["account_info"] == 1


def test_account_snapshot_cache_invalidates_on_client_swap(monkeypatch):
    """Swapping the read client (key rotation, or a fresh test) must never
    serve a stale snapshot from the previous client."""
    use_mock_read_client(monkeypatch, client=MockReadClient())
    client = make_client(monkeypatch)
    client.get("/api/portfolio/binance/summary")

    call_count = {"account_info": 0}

    class CountingClient(MockReadClient):
        async def get_account_info(self):
            call_count["account_info"] += 1
            return await super().get_account_info()

    use_mock_read_client(monkeypatch, client=CountingClient())
    client.get("/api/portfolio/binance/summary")
    assert call_count["account_info"] == 1  # not served from the old client's cache


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
