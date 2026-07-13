"""Protection watchdog (app/trading/protection_watchdog.py) - detects
unprotected live positions on a periodic scan, exposed via
GET /api/trading/binance/protection-watchdog."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_module
import app.api.trading_control as tc
import app.trading.protection_watchdog as watchdog
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingControl
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    snapshot_service.reset()
    watchdog.LAST_SCAN = {"checked_at": None, "unprotected": [], "available": True, "reason": None}
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


class WatchdogMockClient:
    configured = True
    read_only = True

    def __init__(self, positions=None, open_orders=None):
        self._positions = positions or []
        self._open_orders = open_orders or []

    async def get_account_info(self):
        from app.exchanges.binance_models import BinanceAccountSummary
        return BinanceAccountSummary(
            total_wallet_balance=100.0, available_balance=100.0, total_margin_balance=100.0,
            total_unrealized_pnl=0.0, total_initial_margin=0.0, total_maintenance_margin=0.0,
        )

    async def get_positions(self, symbol=None):
        return self._positions

    async def get_daily_realized_pnl(self):
        return 0.0

    async def get_open_orders(self, symbol=None):
        return self._open_orders


def eth_short_position():
    from app.exchanges.binance_models import BinancePosition
    return BinancePosition(
        symbol="ETHUSDT", side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.30,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )


def test_scan_is_noop_in_paper_mode():
    modes.set_mode(modes.MODE_PAPER)
    result = asyncio.run(watchdog._scan_once())
    assert result["unprotected"] == []
    assert result["available"] is True


def test_scan_detects_unprotected_position(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(portfolio_module, "_read_client", WatchdogMockClient(positions=[eth_short_position()]))
    modes.set_mode(modes.MODE_TESTNET)

    result = asyncio.run(watchdog._scan_once())
    assert len(result["unprotected"]) == 1
    assert result["unprotected"][0]["symbol"] == "ETHUSDT"
    assert result["unprotected"][0]["protection_status"] == "MISSING_TP_SL"


def test_scan_reports_clean_when_position_is_protected(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class FakeOrder:
        symbol: str
        type: str
        order_id: int
        stop_price: float
        reduce_only: bool = True
        close_position: bool = False

    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    orders = [
        FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0),
        FakeOrder(symbol="ETHUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=1792.0),
    ]
    monkeypatch.setattr(portfolio_module, "_read_client", WatchdogMockClient(positions=[eth_short_position()], open_orders=orders))
    modes.set_mode(modes.MODE_TESTNET)

    result = asyncio.run(watchdog._scan_once())
    assert result["unprotected"] == []


def test_watchdog_endpoint_reflects_last_scan(monkeypatch):
    watchdog.LAST_SCAN = {
        "checked_at": "2026-07-12T10:00:00+00:00",
        "unprotected": [{"symbol": "ETHUSDT", "side": "SHORT", "protection_status": "MISSING_TP_SL"}],
        "available": True, "reason": None,
    }
    client = make_client()
    body = client.get("/api/trading/binance/protection-watchdog").json()
    assert body["unprotected"][0]["symbol"] == "ETHUSDT"
    assert body["enabled"] is True  # default
    assert "interval_seconds" in body


def test_watchdog_endpoint_no_secrets(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    client = make_client()
    r = client.get("/api/trading/binance/protection-watchdog")
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


# ============================= Phase 26 Algo Order Provider: repair/close ==

class RepairMockClient(WatchdogMockClient):
    """WatchdogMockClient plus the write surface needed for
    protection_provider.place_protection to actually repair a naked
    position: classic place_stop_loss/place_take_profit (optionally
    failing with -4120) and the raw algo _post/_get/_delete/
    _client_order_id surface."""

    read_only = False

    def __init__(self, *a, classic_fails_with_4120=False, always_fails=False, **kw):
        super().__init__(*a, **kw)
        self._classic_fails = classic_fails_with_4120
        self._always_fails = always_fails
        self._next_algo_id = 9000
        self._algo_orders: dict[int, dict] = {}
        self.calls: list[tuple] = []

    @staticmethod
    def _client_order_id(prefix="qx"):
        import uuid
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    async def get_position_mode(self):
        return False

    async def _leg(self, kind, symbol, stop_price):
        from app.exchanges.binance_errors import BinanceError
        self.calls.append((f"classic_{kind}", symbol, stop_price))
        if self._always_fails:
            raise BinanceError("Insufficient margin", code=-2019, status=400)
        if self._classic_fails:
            raise BinanceError(
                "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.",
                code=-4120, status=400,
            )
        from tests.test_execution_router_binance import make_order
        order_type = "TAKE_PROFIT_MARKET" if kind == "tp" else "STOP_MARKET"
        return make_order(order_id=len(self.calls) + 1, symbol=symbol, type=order_type, stop_price=stop_price, reduce_only=True)

    async def place_take_profit(self, symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        return await self._leg("tp", symbol, stop_price)

    async def place_stop_loss(self, symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        return await self._leg("sl", symbol, stop_price)

    async def _post(self, path, params=None):
        self.calls.append(("_post", path, params))
        if self._always_fails:
            from app.exchanges.binance_errors import BinanceError
            raise BinanceError("Insufficient margin", code=-2019, status=400)
        if path == "/fapi/v1/algoOrder":
            self._next_algo_id += 1
            algo_id = self._next_algo_id
            self._algo_orders[algo_id] = {"status": "NEW", "params": params}
            return {
                "algoId": algo_id, "clientAlgoId": params.get("clientAlgoId", ""), "algoType": "CONDITIONAL",
                "orderType": params["type"], "symbol": params["symbol"], "side": params["side"],
                "positionSide": params.get("positionSide", "BOTH"), "algoStatus": "NEW",
                "triggerPrice": params.get("triggerPrice"), "quantity": params.get("quantity", 0),
            }
        raise AssertionError(f"unexpected POST {path}")

    async def cancel_order(self, symbol, order_id=None, orig_client_order_id=None):
        return None

    async def place_market_order(self, symbol, side, quantity, reduce_only=False, client_order_id=None):
        self.calls.append(("place_market_order", symbol, side, quantity, reduce_only))
        from tests.test_execution_router_binance import make_order
        return make_order(order_id=999, symbol=symbol, side=side, quantity=quantity, executed_qty=quantity, reduce_only=reduce_only)

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def test_watchdog_auto_protect_repairs_via_algo_provider_on_4120(monkeypatch):
    from app.trading.execution_router import router as execution_router_singleton
    from tests.test_execution_router_binance import make_provider
    from app.trading import protection_capability

    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(settings, "binance_auto_protect_positions", True)
    read_client = WatchdogMockClient(positions=[eth_short_position()])
    monkeypatch.setattr(portfolio_module, "_read_client", read_client)
    write_client = RepairMockClient(classic_fails_with_4120=True)
    monkeypatch.setattr(execution_router_singleton, "_live", make_provider(write_client))
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.unlock_live()
    modes.set_mode(modes.MODE_LIVE)

    result = asyncio.run(watchdog._scan_once())

    assert result["repair_failed"] == []
    assert result["critical"] is False
    assert protection_capability.get_provider(modes.MODE_LIVE) == "algo"
    assert write_client.called("_post")


def test_watchdog_reports_critical_and_repair_failed_when_repair_cannot_succeed(monkeypatch):
    from app.trading.execution_router import router as execution_router_singleton
    from tests.test_execution_router_binance import make_provider

    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(settings, "binance_auto_protect_positions", True)
    read_client = WatchdogMockClient(positions=[eth_short_position()])
    monkeypatch.setattr(portfolio_module, "_read_client", read_client)
    write_client = RepairMockClient(always_fails=True)
    monkeypatch.setattr(execution_router_singleton, "_live", make_provider(write_client))
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.unlock_live()
    modes.set_mode(modes.MODE_LIVE)

    result = asyncio.run(watchdog._scan_once())

    assert len(result["repair_failed"]) == 1
    assert result["repair_failed"][0]["symbol"] == "ETHUSDT"
    assert result["critical"] is True


def test_watchdog_closes_position_when_repair_fails_and_configured(monkeypatch):
    from app.trading.execution_router import router as execution_router_singleton
    from tests.test_execution_router_binance import make_provider

    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(settings, "binance_auto_protect_positions", True)
    monkeypatch.setattr(settings, "binance_close_if_protection_fails", True)
    read_client = WatchdogMockClient(positions=[eth_short_position()])
    monkeypatch.setattr(portfolio_module, "_read_client", read_client)
    # close_position looks up the position via the write client too
    write_client = RepairMockClient(always_fails=True, positions=[eth_short_position()])
    monkeypatch.setattr(execution_router_singleton, "_live", make_provider(write_client))
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.unlock_live()
    modes.set_mode(modes.MODE_LIVE)

    result = asyncio.run(watchdog._scan_once())

    assert result["critical"] is True
    assert write_client.called("place_market_order")  # close_position's reduce-only market order
