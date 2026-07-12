"""app/trading/protection.py - deriving TP/SL protection status from real
Binance open orders only, never local DB state."""

from dataclasses import dataclass

import pytest

from app.trading import protection


@dataclass
class FakeOrder:
    symbol: str
    type: str
    order_id: int
    stop_price: float
    reduce_only: bool = False
    close_position: bool = False


def test_protected_when_both_sl_and_tp_present():
    orders = [
        FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0, reduce_only=True),
        FakeOrder(symbol="ETHUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=1790.0, close_position=True),
    ]
    result = protection.derive_protection("ETHUSDT", orders)
    assert result.status == protection.PROTECTED
    assert result.sl_price == 1810.0
    assert result.tp_price == 1790.0
    assert result.sl_order_id == 1
    assert result.tp_order_id == 2


def test_missing_both_when_no_matching_orders():
    result = protection.derive_protection("ETHUSDT", [])
    assert result.status == protection.MISSING_BOTH
    assert protection.is_unprotected(result.status)


def test_missing_tp_when_only_sl_present():
    orders = [FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0, reduce_only=True)]
    result = protection.derive_protection("ETHUSDT", orders)
    assert result.status == protection.MISSING_TP
    assert protection.is_unprotected(result.status)


def test_missing_sl_when_only_tp_present():
    orders = [FakeOrder(symbol="ETHUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=1790.0, reduce_only=True)]
    result = protection.derive_protection("ETHUSDT", orders)
    assert result.status == protection.MISSING_SL


def test_non_reduce_only_order_does_not_count_as_protection():
    """A resting limit order that happens to share the symbol and order
    type must not be mistaken for a protective exit."""
    orders = [FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0,
                        reduce_only=False, close_position=False)]
    result = protection.derive_protection("ETHUSDT", orders)
    assert result.status == protection.MISSING_BOTH


def test_orders_for_other_symbols_are_ignored():
    orders = [
        FakeOrder(symbol="BTCUSDT", type="STOP_MARKET", order_id=1, stop_price=63000.0, reduce_only=True),
        FakeOrder(symbol="BTCUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=65000.0, reduce_only=True),
    ]
    result = protection.derive_protection("ETHUSDT", orders)
    assert result.status == protection.MISSING_BOTH


def test_local_stale_detected_when_db_claims_protection_binance_does_not_show():
    result = protection.derive_protection("ETHUSDT", [], local_sl=1810.0, local_tp=1790.0)
    assert result.local_stale is True
    assert result.status == protection.MISSING_BOTH


def test_not_stale_when_local_and_binance_agree():
    orders = [
        FakeOrder(symbol="ETHUSDT", type="STOP_MARKET", order_id=1, stop_price=1810.0, reduce_only=True),
        FakeOrder(symbol="ETHUSDT", type="TAKE_PROFIT_MARKET", order_id=2, stop_price=1790.0, reduce_only=True),
    ]
    result = protection.derive_protection("ETHUSDT", orders, local_sl=1810.0, local_tp=1790.0)
    assert result.local_stale is False


@pytest.mark.parametrize("status,expected", [
    (protection.PROTECTED, False),
    (protection.MISSING_TP, True),
    (protection.MISSING_SL, True),
    (protection.MISSING_BOTH, True),
])
def test_is_unprotected(status, expected):
    assert protection.is_unprotected(status) is expected
