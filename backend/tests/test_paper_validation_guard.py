"""One-shot PAPER-only validation guard.

Covers exactly the properties the guard exists to prove: it permits one
entry, blocks a second, never blocks position management, restores
maintenance on both a normal close and a timeout, and cannot affect real
execution mode under any circumstance.
"""
import asyncio

import pytest

from app.db.models import PaperValidationGuard, Trade
from app.db.session import SessionLocal
from app.deployment import maintenance
from app.trading import modes, paper_validation_guard as guard


@pytest.fixture(autouse=True)
def _clean_guard_and_maintenance():
    db = SessionLocal()
    try:
        row = db.get(PaperValidationGuard, guard.GUARD_ID)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    maintenance.disable()
    yield
    db = SessionLocal()
    try:
        row = db.get(PaperValidationGuard, guard.GUARD_ID)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    maintenance.disable()


def _force_mode(monkeypatch, mode: str):
    monkeypatch.setattr(guard.modes, "effective_mode", lambda *a, **k: mode)


def test_start_activates_without_touching_maintenance():
    result = guard.start(max_entry_attempts=1, max_holding_seconds=3600, validation_window_seconds=21600)
    assert result["active"] is True
    assert result["entry_attempts"] == 0
    assert maintenance.enabled() is False  # start() never lifts or sets maintenance itself


def test_second_start_while_active_raises():
    guard.start()
    with pytest.raises(RuntimeError):
        guard.start()


def test_guard_permits_exactly_one_entry_attempt(monkeypatch):
    _force_mode(monkeypatch, modes.MODE_PAPER)
    guard.start(max_entry_attempts=1)
    first = asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    assert first is None
    assert guard.status()["entry_attempts"] == 1


def test_second_entry_is_rejected_after_quota_consumed(monkeypatch):
    _force_mode(monkeypatch, modes.MODE_PAPER)
    guard.start(max_entry_attempts=1)
    assert asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT")) is None
    second = asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    assert second == "PAPER_VALIDATION_GUARD_QUOTA_EXHAUSTED"
    # A different symbol is blocked too - the quota is global, not per-symbol.
    other_symbol = asyncio.run(guard.check_and_register_entry_attempt("ETHUSDT"))
    assert other_symbol == "PAPER_VALIDATION_GUARD_QUOTA_EXHAUSTED"


def test_second_entry_rejected_once_entry_is_accepted(monkeypatch):
    _force_mode(monkeypatch, modes.MODE_PAPER)
    guard.start(max_entry_attempts=5)  # even a generous quota is moot once one position is open
    assert asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT")) is None
    guard.record_entry_outcome("BTCUSDT", accepted=True, trade_id=1)
    second = asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    assert second == "PAPER_VALIDATION_GUARD_POSITION_ALREADY_OPEN"


def test_guard_is_a_no_op_for_live_mode_even_when_exhausted(monkeypatch):
    """The guard cannot affect real execution mode: with the quota fully
    consumed under PAPER, switching to LIVE must still see no block at all -
    not "different reason", literally no interaction."""
    _force_mode(monkeypatch, modes.MODE_PAPER)
    guard.start(max_entry_attempts=1)
    asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    assert asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT")) == "PAPER_VALIDATION_GUARD_QUOTA_EXHAUSTED"
    _force_mode(monkeypatch, modes.MODE_LIVE)
    live_result = asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    assert live_result is None
    # And the attempt counter must be untouched by the live-mode call.
    _force_mode(monkeypatch, modes.MODE_PAPER)
    assert guard.status()["entry_attempts"] == 1


def test_guard_inactive_is_a_no_op_regardless_of_mode(monkeypatch):
    _force_mode(monkeypatch, modes.MODE_PAPER)
    assert asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT")) is None
    assert guard.status() == {"active": False}


def test_watchdog_restores_maintenance_when_the_tracked_trade_closes():
    guard.start(max_entry_attempts=1, max_holding_seconds=3600, validation_window_seconds=21600)
    db = SessionLocal()
    try:
        trade = Trade(symbol="BTCUSDT", side="LONG", entry=100.0, qty=0.01, status="CLOSED", exit=101.0, pnl=1.0)
        db.add(trade)
        db.commit()
        trade_id = trade.id
    finally:
        db.close()
    guard.record_entry_outcome("BTCUSDT", accepted=True, trade_id=trade_id)
    assert maintenance.enabled() is False
    asyncio.run(guard.watchdog_tick())
    assert maintenance.enabled() is True
    status = guard.status()
    assert status["active"] is False
    assert status["completed"] is True
    assert status["completed_reason"] == "trade_closed"


def test_watchdog_restores_maintenance_on_window_expiry_with_no_entry():
    guard.start(max_entry_attempts=1, validation_window_seconds=1)
    db = SessionLocal()
    try:
        row = db.get(PaperValidationGuard, guard.GUARD_ID)
        from datetime import datetime, timedelta, timezone
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    asyncio.run(guard.watchdog_tick())
    assert maintenance.enabled() is True
    status = guard.status()
    assert status["active"] is False
    assert status["completed_reason"] == "validation_window_expired"


def test_watchdog_force_closes_and_restores_after_holding_time_exceeded(monkeypatch):
    guard.start(max_entry_attempts=1, max_holding_seconds=1, validation_window_seconds=21600)
    db = SessionLocal()
    try:
        trade = Trade(symbol="BTCUSDT", side="LONG", entry=100.0, qty=0.01, status="OPEN")
        db.add(trade)
        db.commit()
        trade_id = trade.id
    finally:
        db.close()
    from datetime import datetime, timedelta, timezone
    db = SessionLocal()
    try:
        row = db.get(PaperValidationGuard, guard.GUARD_ID)
        row.entry_accepted = True
        row.entry_symbol = "BTCUSDT"
        row.entry_trade_id = trade_id
        row.entry_accepted_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()

    class _StubResult:
        ok = True
        reason = None

    async def _fake_close(**kwargs):
        db2 = SessionLocal()
        try:
            t = db2.get(Trade, kwargs["position_id"])
            t.status = "CLOSED"
            db2.commit()
        finally:
            db2.close()
        return _StubResult()

    from app.trading import execution_router as router_module
    monkeypatch.setattr(router_module.router, "close_position", _fake_close)
    asyncio.run(guard.watchdog_tick())
    assert maintenance.enabled() is True
    status = guard.status()
    assert status["completed_reason"] == "holding_time_exceeded"
    db = SessionLocal()
    try:
        assert db.get(Trade, trade_id).status == "CLOSED"
    finally:
        db.close()


def test_position_management_is_never_gated_by_the_guard(monkeypatch):
    """close_position must remain callable and unaffected regardless of
    guard state - management of an already-open position continues even
    after the entry quota is fully consumed."""
    _force_mode(monkeypatch, modes.MODE_PAPER)
    guard.start(max_entry_attempts=1)
    asyncio.run(guard.check_and_register_entry_attempt("BTCUSDT"))
    guard.record_entry_outcome("BTCUSDT", accepted=True, trade_id=99)

    from app.trading.execution_router import router

    called = {}

    async def _fake_provider_close(**kwargs):
        called.update(kwargs)
        from app.trading.execution_router import RouterResult
        return RouterResult(ok=True, mode=modes.MODE_PAPER, action="close_position")

    monkeypatch.setattr(router._paper, "close_position", _fake_provider_close)
    result = asyncio.run(router.close_position(position_id=99, symbol="BTCUSDT"))
    assert result.ok is True
    assert called.get("position_id") == 99
