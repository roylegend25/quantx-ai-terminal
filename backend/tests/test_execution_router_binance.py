"""Execution router against a fully mocked Binance testnet client: the
order route, real TP/SL placement, cancel-and-replace edits, position sync
and validation - no network, no keys."""

import asyncio

import pytest

from app.db.session import SessionLocal
from app.db.models import ExchangePositionRow, TradingControl
from app.exchanges.binance_models import BinanceBalance, BinanceOrder, BinancePosition
from app.trading import modes, real_risk_gate
from app.trading.execution_router import BinanceExecutionProvider


def make_order(order_id=1, **kw):
    defaults = dict(
        order_id=order_id, client_order_id=f"qx-{order_id}", symbol="BTCUSDT", side="BUY",
        position_side="BOTH", type="MARKET", status="FILLED", price=0.0, stop_price=0.0,
        quantity=0.001, executed_qty=0.001, avg_price=50000.0, reduce_only=False,
        close_position=False,
    )
    defaults.update(kw)
    return BinanceOrder(**defaults)


class MockBinanceClient:
    """Records every call; behaves like a healthy testnet account with one
    optional open position."""

    testnet = True
    configured = True

    def __init__(self, position=None, open_orders=None):
        self.calls: list[tuple] = []
        self.position = position
        self.open_orders = open_orders or []
        self._next_order_id = 100

    def _order(self, **kw):
        self._next_order_id += 1
        return make_order(order_id=self._next_order_id, **kw)

    async def ping(self):
        return True

    async def get_daily_realized_pnl(self):
        return 0.0

    async def get_balances(self):
        return [BinanceBalance(asset="USDT", balance=1000.0, available=1000.0)]

    async def get_mark_price(self, symbol):
        self.calls.append(("get_mark_price", symbol))
        return 50000.0

    async def get_positions(self, symbol=None):
        self.calls.append(("get_positions", symbol))
        return [self.position] if self.position else []

    async def get_open_orders(self, symbol=None):
        self.calls.append(("get_open_orders", symbol))
        return list(self.open_orders)

    async def set_margin_type(self, symbol, margin_type="ISOLATED"):
        self.calls.append(("set_margin_type", symbol, margin_type))
        return {}

    async def set_leverage(self, symbol, leverage):
        self.calls.append(("set_leverage", symbol, leverage))
        return {}

    async def place_market_order(self, symbol, side, quantity, reduce_only=False, client_order_id=None):
        self.calls.append(("place_market_order", symbol, side, quantity, reduce_only))
        return self._order(symbol=symbol, side=side, quantity=quantity, executed_qty=quantity)

    async def place_stop_loss(self, symbol, position_side, stop_price, quantity=None, client_order_id=None):
        self.calls.append(("place_stop_loss", symbol, position_side, stop_price))
        return self._order(symbol=symbol, type="STOP_MARKET", stop_price=stop_price, reduce_only=True)

    async def place_take_profit(self, symbol, position_side, stop_price, quantity=None, client_order_id=None):
        self.calls.append(("place_take_profit", symbol, position_side, stop_price))
        return self._order(symbol=symbol, type="TAKE_PROFIT_MARKET", stop_price=stop_price, reduce_only=True)

    async def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))
        return make_order(order_id=order_id, status="CANCELED")

    async def cancel_all_orders(self, symbol):
        self.calls.append(("cancel_all_orders", symbol))
        return {}

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


def btc_long_position():
    return BinancePosition(
        symbol="BTCUSDT", side="LONG", quantity=0.002, entry_price=50000.0, mark_price=50000.0,
        leverage=2.0, margin_type="isolated", liquidation_price=40000.0,
        unrealized_pnl=0.0, margin_used=50.0, notional=100.0,
    )


def make_provider(mock_client):
    provider = BinanceExecutionProvider(testnet=True)
    provider._client = mock_client
    return provider


@pytest.fixture(autouse=True)
def testnet_mode():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def test_testnet_open_places_market_order_with_leverage_and_protective_orders(monkeypatch):
    from app.core.config import settings
    # $100 notional = 0.002 BTC at the mocked $50k mark - above the 0.001
    # step size a $20 order would round down through
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0,
        sl=48000.0, tp=55000.0,
    ))

    assert result.ok, result.reason
    assert client.called("set_margin_type")
    assert client.called("set_leverage")[0] == ("set_leverage", "BTCUSDT", 2)
    market = client.called("place_market_order")[0]
    assert market[1] == "BTCUSDT" and market[2] == "BUY"
    assert client.called("place_stop_loss")[0][3] == 48000.0
    assert client.called("place_take_profit")[0][3] == 55000.0
    assert result.detail["sl_order_id"] and result.detail["tp_order_id"]


def test_testnet_open_blocked_by_gate_places_nothing():
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=999999.0, leverage=1.0,
    ))

    assert not result.ok
    assert "exceeds configured max" in result.reason
    assert not client.called("place_market_order")


def test_close_position_is_reduce_only():
    client = MockBinanceClient(position=btc_long_position())
    provider = make_provider(client)

    result = asyncio.run(provider.close_position(symbol="BTCUSDT"))

    assert result.ok, result.reason
    market = client.called("place_market_order")[0]
    # closing a LONG sells, reduce-only
    assert market[2] == "SELL"
    assert market[4] is True


def test_update_stop_loss_cancels_old_and_places_new_long():
    old_sl = make_order(order_id=7, type="STOP_MARKET", stop_price=47000.0)
    client = MockBinanceClient(position=btc_long_position(), open_orders=[old_sl])
    provider = make_provider(client)

    result = asyncio.run(provider.update_stop_loss(symbol="BTCUSDT", stop_loss=48500.0))

    assert result.ok, result.reason
    assert client.called("cancel_order")[0][2] == 7
    assert client.called("place_stop_loss")[0][3] == 48500.0


def test_update_stop_loss_validates_side_long():
    client = MockBinanceClient(position=btc_long_position())
    provider = make_provider(client)
    # SL above mark on a LONG is invalid
    result = asyncio.run(provider.update_stop_loss(symbol="BTCUSDT", stop_loss=51000.0))
    assert not result.ok
    assert "stop loss" in result.reason.lower()
    assert not client.called("place_stop_loss")


def test_update_take_profit_validates_side_short():
    short = btc_long_position()
    short.side = "SHORT"
    client = MockBinanceClient(position=short)
    provider = make_provider(client)
    # TP above mark on a SHORT is invalid
    result = asyncio.run(provider.update_take_profit(symbol="BTCUSDT", take_profit=52000.0))
    assert not result.ok
    assert "take profit" in result.reason.lower()
    assert not client.called("place_take_profit")


def test_sync_positions_mirrors_binance_as_source_of_truth():
    client = MockBinanceClient(position=btc_long_position())
    provider = make_provider(client)

    result = asyncio.run(provider.sync_positions())
    assert result.ok and result.detail["synced"] == 1

    db = SessionLocal()
    try:
        rows = db.query(ExchangePositionRow).all()
        assert len(rows) == 1
        assert rows[0].symbol == "BTCUSDT"
        assert rows[0].quantity == 0.002
        assert rows[0].mode == modes.MODE_TESTNET
    finally:
        db.close()

    # position closed on Binance -> local mirror row removed on next sync
    client.position = None
    asyncio.run(provider.sync_positions())
    db = SessionLocal()
    try:
        assert db.query(ExchangePositionRow).count() == 0
    finally:
        db.close()


def test_cancel_all_orders_covers_allowed_symbols():
    client = MockBinanceClient()
    provider = make_provider(client)
    result = asyncio.run(provider.cancel_all_orders())
    assert result.ok
    assert {c[1] for c in client.called("cancel_all_orders")} == {"BTCUSDT", "ETHUSDT"}
