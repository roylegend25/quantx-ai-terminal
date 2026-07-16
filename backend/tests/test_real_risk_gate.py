"""The real-trading pre-flight gate (app/trading/real_risk_gate.py) with a
fully mocked Binance client - no network, no keys."""

import asyncio

import pytest

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingControl
from app.exchanges.binance_models import BinanceBalance
from app.trading import modes, real_risk_gate


class FakeClient:
    """Healthy, well-funded account by default - no open positions, so the
    Phase 27 existing-position-protection check always passes unless a test
    explicitly seeds positions/open_orders."""

    def __init__(self, available_usdt=1000.0, daily_pnl=0.0, reachable=True, positions=None, open_orders=None):
        self.available_usdt = available_usdt
        self.daily_pnl = daily_pnl
        self.reachable = reachable
        self.positions = positions or []
        self.open_orders = open_orders or []

    async def ping(self):
        if not self.reachable:
            raise ConnectionError("down")
        return True

    async def get_account_info(self):
        from app.exchanges.binance_models import BinanceAccountSummary
        return BinanceAccountSummary(
            total_wallet_balance=self.available_usdt, available_balance=self.available_usdt,
            total_margin_balance=self.available_usdt, total_unrealized_pnl=0.0,
            total_initial_margin=0.0, total_maintenance_margin=0.0,
        )

    async def get_daily_realized_pnl(self):
        return self.daily_pnl

    async def get_balances(self):
        return [BinanceBalance(asset="USDT", balance=self.available_usdt, available=self.available_usdt)]

    async def get_positions(self, symbol=None):
        return self.positions

    async def get_open_orders(self, symbol=None):
        return self.open_orders


@pytest.fixture(autouse=True)
def testnet_mode():
    original_unprotected_gate = settings.binance_block_new_trades_if_unprotected
    settings.binance_block_new_trades_if_unprotected = True
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    settings.binance_block_new_trades_if_unprotected = original_unprotected_gate
    modes.set_mode(modes.MODE_PAPER)


def run_gate(client=None, **kwargs):
    # sl/tp default to a valid LONG pair (Phase 27: BINANCE_REQUIRE_TP_SL
    # defaults true) - tests exercising a SHORT order override both.
    defaults = dict(symbol="BTCUSDT", side="LONG", notional_usdt=10.0, leverage=1.0, sl=45000.0, tp=55000.0)
    defaults.update(kwargs)
    return asyncio.run(real_risk_gate.evaluate_real_order(client=client or FakeClient(), **defaults))


def test_healthy_order_passes():
    result = run_gate()
    assert result.allowed, result.reason


def test_max_notional_blocks_oversized_order(monkeypatch):
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 25.0)
    result = run_gate(notional_usdt=26.0)
    assert not result.allowed
    assert "exceeds configured max" in result.reason


def test_symbol_not_in_allowlist_blocked():
    result = run_gate(symbol="DOGEUSDT")
    assert not result.allowed
    assert "BINANCE_ALLOWED_SYMBOLS" in result.reason


def test_leverage_above_max_blocked(monkeypatch):
    monkeypatch.setattr(settings, "binance_max_leverage", 3.0)
    result = run_gate(leverage=5.0)
    assert not result.allowed
    assert "exceeds configured max" in result.reason


def test_low_confidence_blocked():
    # default risk settings require 60% confidence
    result = run_gate(confidence=10.0)
    assert not result.allowed
    assert "below required" in result.reason


def test_unknown_confidence_fails_open():
    assert run_gate(confidence=None).allowed


def test_bad_data_quality_blocked():
    result = run_gate(data_reliable=False)
    assert not result.allowed
    assert "data quality" in result.reason.lower()


def test_wide_spread_blocked():
    result = run_gate(spread_pct=2.0)
    assert not result.allowed
    assert "spread" in result.reason.lower()


def test_daily_loss_limit_blocked(monkeypatch):
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 20.0)
    result = run_gate(client=FakeClient(daily_pnl=-25.0))
    assert not result.allowed
    assert "Daily loss limit" in result.reason


def test_insufficient_balance_blocked():
    result = run_gate(client=FakeClient(available_usdt=1.0), notional_usdt=10.0, leverage=1.0)
    assert not result.allowed
    assert "Insufficient balance" in result.reason


def test_exchange_unreachable_blocked():
    result = run_gate(client=FakeClient(reachable=False))
    assert not result.allowed
    assert "unreachable" in result.reason.lower()


def test_duplicate_order_blocked():
    first = run_gate()
    assert first.allowed
    second = run_gate()
    assert not second.allowed
    assert "Duplicate" in second.reason


def test_kill_switch_blocks_gate():
    modes.set_kill_switch(True, reason="test")
    try:
        result = run_gate()
        assert not result.allowed
        assert "Kill switch" in result.reason
    finally:
        modes.set_kill_switch(False)


def test_max_open_positions_blocked():
    # default risk settings allow 1 open position
    result = run_gate(open_positions=1)
    assert not result.allowed
    assert "Maximum open positions" in result.reason


# ------------------------- Debug 2.pdf section 7: no pyramiding a symbol

def test_existing_position_on_symbol_blocked():
    """Root cause of the production loop where the bot re-signaled ETHUSDT
    every cycle and each attempt failed at Binance validation with
    'Position side cannot be changed if there exists open orders.' -
    margin type/leverage can't be re-asserted while a position already
    exists for the symbol, so a duplicate entry was always doomed. The
    gate must block it before it ever reaches Binance."""
    result = run_gate(existing_position_on_symbol=True)
    assert not result.allowed
    assert "already has an open live position" in result.reason
    check = next(c for c in result.checks if c["check"] == "existing_position_same_symbol")
    assert check["passed"] is False


def test_no_existing_position_on_symbol_passes():
    result = run_gate(existing_position_on_symbol=False)
    assert result.allowed, result.reason
    check = next(c for c in result.checks if c["check"] == "existing_position_same_symbol")
    assert check["passed"] is True


def test_existing_position_on_different_symbol_does_not_block():
    """The block is symbol-specific - a position open on BTCUSDT must never
    prevent a fresh, legitimate entry on ETHUSDT."""
    result = run_gate(symbol="ETHUSDT", existing_position_on_symbol=False)
    assert result.allowed, result.reason


# ------------------------------------------------- Phase 27: TP/SL required

def test_live_entry_without_take_profit_blocked():
    result = run_gate(tp=None)
    assert not result.allowed
    assert "requires TP and SL" in result.reason


def test_live_entry_without_stop_loss_blocked():
    result = run_gate(sl=None)
    assert not result.allowed
    assert "requires TP and SL" in result.reason


def test_live_entry_without_either_tp_sl_blocked():
    result = run_gate(sl=None, tp=None)
    assert not result.allowed
    assert "requires TP and SL" in result.reason


def test_tp_sl_not_required_when_config_disabled(monkeypatch):
    monkeypatch.setattr(settings, "binance_require_tp_sl", False)
    result = run_gate(sl=None, tp=None)
    assert result.allowed, result.reason


def test_invalid_short_take_profit_blocked():
    """SHORT: take profit must be below entry - a TP above the SL (i.e.
    swapped for the side) is rejected before any exchange call."""
    result = run_gate(side="SHORT", sl=1810.0, tp=1830.0)  # tp above sl - wrong for SHORT
    assert not result.allowed
    assert "SHORT" in result.reason


def test_invalid_short_stop_loss_blocked():
    result = run_gate(side="SHORT", sl=1780.0, tp=1790.0)  # sl below tp - wrong for SHORT
    assert not result.allowed
    assert "SHORT" in result.reason


def test_valid_short_tp_sl_passes():
    result = run_gate(side="SHORT", sl=1830.0, tp=1780.0)
    assert result.allowed, result.reason


def test_valid_long_tp_sl_passes():
    result = run_gate(side="LONG", sl=45000.0, tp=55000.0)
    assert result.allowed, result.reason


# --------------------------------------- Phase 27: existing unprotected position

def test_existing_unprotected_position_blocks_new_entries():
    from app.exchanges.binance_models import BinancePosition

    unprotected = BinancePosition(
        symbol="ETHUSDT", side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.3,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )
    client = FakeClient(positions=[unprotected], open_orders=[])
    result = run_gate(client=client)
    assert not result.allowed
    assert "unprotected" in result.reason.lower()


def test_existing_protected_position_does_not_block_new_entries():
    from dataclasses import dataclass

    from app.exchanges.binance_models import BinancePosition

    @dataclass
    class FakeOrder:
        symbol: str
        type: str
        order_id: int
        stop_price: float
        reduce_only: bool = True
        close_position: bool = False

    protected = BinancePosition(
        symbol="ETHUSDT", side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.3,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )
    orders = [
        FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0),
        FakeOrder(symbol="ETHUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=1792.0),
    ]
    client = FakeClient(positions=[protected], open_orders=orders)
    result = run_gate(client=client)
    assert result.allowed, result.reason


def test_block_new_trades_if_unprotected_can_be_disabled(monkeypatch):
    from app.exchanges.binance_models import BinancePosition

    monkeypatch.setattr(settings, "binance_block_new_trades_if_unprotected", False)
    unprotected = BinancePosition(
        symbol="ETHUSDT", side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.3,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )
    client = FakeClient(positions=[unprotected], open_orders=[])
    result = run_gate(client=client)
    assert result.allowed, result.reason
