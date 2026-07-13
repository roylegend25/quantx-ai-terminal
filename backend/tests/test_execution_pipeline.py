"""Execution-pipeline instrumentation (Phase 25): every
BinanceExecutionProvider.open_position() call must leave a stage-by-stage
BinanceExecutionAttempt row behind, regardless of outcome, so a "signal
allowed but no order appeared" situation is never invisible. See
app/trading/execution_pipeline.py."""

import asyncio

import pytest

from app.db.session import SessionLocal
from app.db.models import (
    BinanceExecutionAttempt,
    BinanceProtectionCapability,
    ExchangePositionRow,
    TradingControl,
)
from app.trading import modes, real_risk_gate
from app.trading.execution_pipeline import STAGE_ORDER, classify_binance_error
from app.trading.execution_router import BinanceExecutionProvider
from tests.test_execution_router_binance import (
    MockBinanceClient,
    btc_long_position,
    make_order,
    make_provider,
)


@pytest.fixture(autouse=True)
def testnet_mode():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.query(BinanceExecutionAttempt).delete()
        # A prior test FILE (e.g. test_execution_router_binance.py's -4120
        # migration test) may leave BINANCE_TESTNET permanently pinned to
        # the Algo provider - this file's MockBinanceClient is classic-only,
        # so it must start from a clean, undetected capability every time,
        # not just whatever the previous module happened to leave behind.
        db.query(BinanceProtectionCapability).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def latest_attempt() -> BinanceExecutionAttempt:
    db = SessionLocal()
    try:
        row = db.query(BinanceExecutionAttempt).order_by(BinanceExecutionAttempt.id.desc()).first()
        assert row is not None, "expected a BinanceExecutionAttempt row to have been written"
        db.expunge(row)
        return row
    finally:
        db.close()


def test_successful_open_records_all_stages_as_success(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0,
        confidence=90.0, decision_engine={"active_model": {"model_type": "xgboost"}},
        sl=48000.0, tp=55000.0,
    ))
    assert result.ok, result.reason

    attempt = latest_attempt()
    assert attempt.final_status == "order_accepted"
    assert attempt.order_id is not None
    assert attempt.symbol == "BTCUSDT"
    assert attempt.is_test is False
    stage_names = [s["stage"] for s in attempt.stages]
    # every documented stage is present exactly once; recording order follows
    # actual execution (position sizing before the risk gate evaluates the
    # clamped notional), which the frontend re-orders into STAGE_ORDER for
    # display - so assert set equality plus the two structural anchors.
    assert set(stage_names) == set(STAGE_ORDER)
    assert len(stage_names) == len(STAGE_ORDER)
    assert stage_names[0] == "signal_generated"
    assert stage_names[-1] == "trade_active"
    assert all(s["status"] == "success" for s in attempt.stages)
    assert attempt.adjusted_quantity == pytest.approx(0.002, abs=1e-6)
    assert attempt.latency_ms is not None and attempt.latency_ms >= 0


def test_champion_model_stage_is_waiting_without_decision_engine(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()
    provider = make_provider(client)

    asyncio.run(provider.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0))

    attempt = latest_attempt()
    champion_stage = next(s for s in attempt.stages if s["stage"] == "champion_model")
    assert champion_stage["status"] == "waiting"
    assert champion_stage["reason"]


def test_risk_gate_rejection_records_failed_stage_with_classified_reason():
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=999999.0, leverage=1.0, sl=48000.0, tp=55000.0,
    ))
    assert not result.ok

    attempt = latest_attempt()
    assert attempt.final_status == "failed"
    risk_gate_stage = next(s for s in attempt.stages if s["stage"] == "risk_gate")
    assert risk_gate_stage["status"] == "failed"
    assert risk_gate_stage["reason"] == "Risk gate rejected"  # max_notional isn't a specifically-labeled check
    # stages after risk_gate were never reached
    stage_names = [s["stage"] for s in attempt.stages]
    assert "quantity_calculation" not in stage_names


def test_low_confidence_maps_to_confidence_dropped(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=1.0, confidence=1.0,
        sl=48000.0, tp=55000.0,
    ))
    assert not result.ok

    attempt = latest_attempt()
    risk_gate_stage = next(s for s in attempt.stages if s["stage"] == "risk_gate")
    assert risk_gate_stage["reason"] == "Confidence dropped"


def test_quantity_below_minimum_records_failed_stage(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()
    provider = make_provider(client)

    # $0.01 notional at $50k mark rounds to 0 BTC at 3-decimal precision
    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=0.01, leverage=1.0, sl=45000.0, tp=55000.0,
    ))
    assert not result.ok
    assert "below the minimum order size" in result.reason

    attempt = latest_attempt()
    assert attempt.final_status == "failed"
    qty_stage = next(s for s in attempt.stages if s["stage"] == "quantity_calculation")
    assert qty_stage["status"] == "failed"
    assert qty_stage["reason"] == "Quantity below minimum"


def test_binance_validation_failure_records_failed_stage(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()

    async def fail_leverage(symbol, leverage):
        from app.exchanges.binance_errors import BinanceInvalidLeverage
        raise BinanceInvalidLeverage("Invalid leverage", code=-4028)

    client.set_leverage = fail_leverage
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0, sl=48000.0, tp=55000.0,
    ))
    assert not result.ok

    attempt = latest_attempt()
    assert attempt.final_status == "failed"
    validation_stage = next(s for s in attempt.stages if s["stage"] == "binance_validation")
    assert validation_stage["status"] == "failed"
    stage_names = [s["stage"] for s in attempt.stages]
    assert "entry_order_submitted" not in stage_names
    assert not client.called("place_market_order")


def test_order_submission_failure_records_failed_stage_with_binance_rejection(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()

    async def fail_order(symbol, side, quantity, reduce_only=False, client_order_id=None):
        from app.exchanges.binance_errors import BinanceInsufficientBalance
        raise BinanceInsufficientBalance("Margin is insufficient", code=-2019)

    client.place_market_order = fail_order
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0, sl=48000.0, tp=55000.0,
    ))
    assert not result.ok

    attempt = latest_attempt()
    assert attempt.final_status == "failed"
    submit_stage = next(s for s in attempt.stages if s["stage"] == "entry_order_submitted")
    assert submit_stage["status"] == "failed"
    assert submit_stage["reason"] == "Insufficient margin"
    assert "entry_order_accepted" not in [s["stage"] for s in attempt.stages]


def test_protection_failure_after_fill_marks_protection_failed_not_order_rejected(monkeypatch):
    """TP/SL placement failing after the exchange already accepted the
    entry must be recorded as PROTECTION_FAILED - the order itself DID
    reach Binance (order_id is set), it's just left unprotected, which is
    a materially different (and more urgent) situation than the order
    never having been submitted at all."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    client = MockBinanceClient()

    async def fail_sl(symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        raise RuntimeError("network blip placing stop loss")

    client.place_stop_loss = fail_sl
    provider = make_provider(client)

    result = asyncio.run(provider.open_position(
        symbol="BTCUSDT", side="LONG", notional_usdt=100.0, leverage=2.0, sl=48000.0, tp=55000.0,
    ))
    assert not result.ok  # RouterResult still reflects the overall failure
    assert result.detail["protection_status"] != "PROTECTED"

    attempt = latest_attempt()
    assert attempt.final_status == "PROTECTION_FAILED"
    assert attempt.order_id is not None  # the entry itself succeeded
    sl_stage = next(s for s in attempt.stages if s["stage"] == "protective_sl_submitted")
    assert sl_stage["status"] == "failed"
    tp_stage = next(s for s in attempt.stages if s["stage"] == "protective_tp_submitted")
    assert tp_stage["status"] == "success"
    confirmed_stage = next(s for s in attempt.stages if s["stage"] == "protection_confirmed")
    assert confirmed_stage["status"] == "failed"


@pytest.mark.parametrize("code,expected", [
    (-2019, "Insufficient margin"),
    (-4015, "Duplicate order protection"),
    (-1121, "Symbol disabled"),
    (-1022, "Signature invalid"),
    (-2022, "Reduce-only conflict"),
    (-4061, "Position mode mismatch"),
    (-1013, "Precision mismatch"),
])
def test_classify_binance_error_covers_documented_reasons(code, expected):
    from app.exchanges.binance_errors import map_binance_error
    err = map_binance_error(400, {"code": code, "msg": "simulated"})
    assert classify_binance_error(err) == expected
