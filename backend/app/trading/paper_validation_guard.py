"""One-shot PAPER-only validation guard.

Bounds exactly one authorized paper-trade lifecycle: at most one entry
attempt, at most one open position, a maximum signal-waiting window, and a
maximum holding time - after which maintenance is automatically restored.

This module never touches live mode, live credentials, live_unlocked, or
the live execution lease system. It only ever calls
app.deployment.maintenance (already used for the unrelated deploy-safety
marker) and reads/writes its own singleton row plus the paper Trade table.
That is a structural guarantee, not just a convention: nothing here can
reach app.trading.modes.set_mode/unlock/live_authorization_leases.

State lives in the DB (not in memory) specifically so restoration survives
an SSH/tmux/Claude disconnection - a background watchdog loop inside the
running app process (registered in app.main) enforces the window/holding
timeout/close-detection independently of any external session, and
startup_recovery() fails closed if a stale guard is ever found active after
a container restart.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.db.models import PaperValidationGuard, Trade
from app.db.session import SessionLocal
from app.deployment import maintenance
from app.monitoring.logging import get_logger, log_event
from app.trading import modes

logger = get_logger("quantx.paper_validation_guard")
GUARD_ID = 1
WATCHDOG_INTERVAL_SECONDS = 20
RUNNING = False


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip even though every datetime here is
    always written as UTC-aware (same class of bug as elsewhere in this
    codebase, e.g. decision_engine.v2._current_edge) - a naive read still
    means UTC, never local time."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _row(db) -> PaperValidationGuard | None:
    return db.get(PaperValidationGuard, GUARD_ID)


def status(db=None) -> dict:
    owns_db = db is None
    db = db or SessionLocal()
    try:
        row = _row(db)
        if row is None:
            return {"active": False}
        return {
            "active": row.active,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "max_entry_attempts": row.max_entry_attempts, "entry_attempts": row.entry_attempts,
            "max_positions": row.max_positions, "max_symbols": row.max_symbols,
            "max_holding_seconds": row.max_holding_seconds,
            "entry_accepted": row.entry_accepted, "entry_symbol": row.entry_symbol,
            "entry_trade_id": row.entry_trade_id,
            "entry_accepted_at": row.entry_accepted_at.isoformat() if row.entry_accepted_at else None,
            "completed": row.completed, "completed_reason": row.completed_reason,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "maintenance_restored_at": row.maintenance_restored_at.isoformat() if row.maintenance_restored_at else None,
        }
    finally:
        if owns_db:
            db.close()


def is_active(db=None) -> bool:
    owns_db = db is None
    db = db or SessionLocal()
    try:
        row = _row(db)
        return bool(row and row.active)
    finally:
        if owns_db:
            db.close()


def start(*, max_entry_attempts: int = 1, max_positions: int = 1, max_symbols: int = 1,
          max_holding_seconds: int = 3600, validation_window_seconds: int = 21600,
          reason: str = "paper_validation_lifecycle") -> dict:
    """Activates the guard. Raises if one is already active - two
    overlapping validation windows are never allowed. Does NOT itself lift
    maintenance; the caller only does that after this returns successfully,
    so the guard is provably active before any window opens."""
    db = SessionLocal()
    try:
        row = _row(db)
        if row is not None and row.active:
            raise RuntimeError("A paper validation guard is already active")
        now = datetime.now(timezone.utc)
        if row is None:
            row = PaperValidationGuard(id=GUARD_ID)
            db.add(row)
        row.active = True
        row.started_at = now
        row.expires_at = now + timedelta(seconds=validation_window_seconds)
        row.max_entry_attempts = max_entry_attempts
        row.entry_attempts = 0
        row.max_positions = max_positions
        row.max_symbols = max_symbols
        row.max_holding_seconds = max_holding_seconds
        row.entry_accepted = False
        row.entry_symbol = None
        row.entry_trade_id = None
        row.entry_accepted_at = None
        row.completed = False
        row.completed_reason = None
        row.completed_at = None
        row.maintenance_restored_at = None
        row.updated_at = now
        db.commit()
        log_event(logger, message="paper_validation_guard_started", category="trading",
                  expires_at=row.expires_at.isoformat(), max_entry_attempts=max_entry_attempts,
                  max_holding_seconds=max_holding_seconds, reason=reason)
        return status(db)
    finally:
        db.close()


async def check_and_register_entry_attempt(symbol: str) -> str | None:
    """Called from ExecutionRouter.open_position immediately before it
    would submit a real (paper) order. Returns a block reason, or None if
    the attempt is allowed - allowing ATOMICALLY consumes the one-shot
    attempt quota in the same commit, so a second concurrent call can never
    also see it as allowed. Structurally inert outside PAPER mode and when
    no guard is active (both cheap, most-common no-ops): this can never
    affect LIVE, TESTNET, or a locked mode."""
    if modes.effective_mode() != modes.MODE_PAPER:
        return None
    db = SessionLocal()
    try:
        row = _row(db)
        if row is None or not row.active:
            return None
        now = datetime.now(timezone.utc)
        if now >= _aware(row.expires_at):
            return "PAPER_VALIDATION_WINDOW_EXPIRED"
        if row.entry_accepted:
            return "PAPER_VALIDATION_GUARD_POSITION_ALREADY_OPEN"
        if row.entry_attempts >= row.max_entry_attempts:
            return "PAPER_VALIDATION_GUARD_QUOTA_EXHAUSTED"
        row.entry_attempts += 1
        row.updated_at = now
        db.commit()
        return None
    finally:
        db.close()


def record_entry_outcome(symbol: str, accepted: bool, trade_id: int | None) -> None:
    """Called right after the one permitted attempt returns, success or
    failure - either way the attempt is already consumed by
    check_and_register_entry_attempt."""
    db = SessionLocal()
    try:
        row = _row(db)
        if row is None or not row.active:
            return
        now = datetime.now(timezone.utc)
        if accepted:
            row.entry_accepted = True
            row.entry_symbol = symbol
            row.entry_trade_id = trade_id
            row.entry_accepted_at = now
        row.updated_at = now
        db.commit()
        log_event(logger, message="paper_validation_guard_entry_recorded", category="trading",
                  symbol=symbol, accepted=accepted, trade_id=trade_id)
    finally:
        db.close()


def _restore_maintenance_and_deactivate(db, row: PaperValidationGuard, reason: str) -> None:
    maintenance.enable(reason=f"paper_validation_guard_complete:{reason}")
    now = datetime.now(timezone.utc)
    row.active = False
    row.completed = True
    row.completed_reason = reason
    row.completed_at = now
    row.maintenance_restored_at = now
    row.updated_at = now
    db.commit()
    log_event(logger, message="paper_validation_guard_completed", category="trading", reason=reason)


async def watchdog_tick() -> None:
    """Idempotent and cheap when inactive (a single row read) - safe to
    call frequently (see the background loop registered in app.main). This
    is the only thing that can restore maintenance for this guard, and it
    runs inside the app process itself, independent of any external
    session that started the validation window."""
    db = SessionLocal()
    try:
        row = _row(db)
        if row is None or not row.active:
            return
        now = datetime.now(timezone.utc)
        if row.entry_accepted and row.entry_trade_id is not None:
            trade = db.get(Trade, row.entry_trade_id)
            if trade is None or trade.status != "OPEN":
                _restore_maintenance_and_deactivate(db, row, "trade_closed")
                return
            held_seconds = (now - _aware(row.entry_accepted_at)).total_seconds()
            if held_seconds > row.max_holding_seconds:
                from app.trading.execution_router import router as execution_router  # local: avoids import cycle
                result = await execution_router.close_position(position_id=row.entry_trade_id, symbol=row.entry_symbol)
                log_event(logger, message="paper_validation_guard_forced_exit", category="trading",
                          trade_id=row.entry_trade_id, ok=result.ok, reason=result.reason)
                _restore_maintenance_and_deactivate(db, row, "holding_time_exceeded")
                return
            return  # position open, within holding budget - keep watching
        if now >= _aware(row.expires_at):
            _restore_maintenance_and_deactivate(db, row, "validation_window_expired")
    finally:
        db.close()


def startup_recovery() -> None:
    """Fail-closed on every app boot, mirroring
    modes.startup_safety_reset(). A guard still marked active after a
    restart either resumes safely (still within its window, position
    traceably open) or is terminated right here - maintenance is never
    left un-restored by omission after a crash/restart."""
    db = SessionLocal()
    try:
        row = _row(db)
        if row is None or not row.active:
            return
        now = datetime.now(timezone.utc)
        if row.entry_accepted and row.entry_trade_id is not None:
            trade = db.get(Trade, row.entry_trade_id)
            stale = (trade is None or trade.status != "OPEN"
                      or now >= _aware(row.expires_at) + timedelta(seconds=row.max_holding_seconds + 300))
            if stale:
                _restore_maintenance_and_deactivate(db, row, "startup_recovery_stale")
            return  # otherwise the watchdog loop resumes it normally
        if now >= _aware(row.expires_at):
            _restore_maintenance_and_deactivate(db, row, "startup_recovery_window_expired")
    finally:
        db.close()


async def _watchdog_loop() -> None:
    while RUNNING:
        try:
            await watchdog_tick()
        except Exception as exc:  # noqa: BLE001 - a watchdog crash must never silently stop enforcing the window
            log_event(logger, message="paper_validation_guard_watchdog_error", level=logging.ERROR,
                      category="trading", error=repr(exc))
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


def start_watchdog() -> None:
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    asyncio.create_task(_watchdog_loop())


def stop_watchdog() -> None:
    global RUNNING
    RUNNING = False
