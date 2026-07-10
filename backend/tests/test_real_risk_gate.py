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
    """Healthy, well-funded account by default."""

    def __init__(self, available_usdt=1000.0, daily_pnl=0.0, reachable=True):
        self.available_usdt = available_usdt
        self.daily_pnl = daily_pnl
        self.reachable = reachable

    async def ping(self):
        if not self.reachable:
            raise ConnectionError("down")
        return True

    async def get_daily_realized_pnl(self):
        return self.daily_pnl

    async def get_balances(self):
        return [BinanceBalance(asset="USDT", balance=self.available_usdt, available=self.available_usdt)]


@pytest.fixture(autouse=True)
def testnet_mode():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def run_gate(client=None, **kwargs):
    defaults = dict(symbol="BTCUSDT", side="LONG", notional_usdt=10.0, leverage=1.0)
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
