"""Persistent, run-scoped live verification safety accounting.

Lifetime BinanceExecutionAttempt rows are immutable audit history. A run
snapshots that lifetime count as its baseline; limits use only the atomic
counter and rows tagged with the run id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.db.models import BinanceExecutionAttempt, LiveVerificationRun, TradingControl

MAX_ATTEMPTS = 4
MAX_COMPLETED_TRADES = 2
MAX_CONSECUTIVE_LOSSES = 2
MAX_REALISED_LOSS_USDT = 0.50
MAX_PLANNED_LOSS_USDT = 0.25
MAX_LEVERAGE = 2.0
MAX_MARGIN_ALLOCATION_PCT = 0.90
MIN_HEADROOM_USDT = 2.50
MAX_RUN_DURATION = timedelta(hours=24)
ACTIVE_STATUSES = ("prepared", "running")


class VerificationBlocked(RuntimeError):
    pass


def _utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def prepare_run(db: Session, *, starting_balance: float, deployment_revision: str | None = None,
                deployment_image_digest: str | None = None, initial_exchange_state: dict | None = None,
                timestamp_sync_state: dict | None = None) -> LiveVerificationRun:
    if starting_balance <= 0:
        raise ValueError("starting_balance must be positive")
    active = db.query(LiveVerificationRun).filter(
        LiveVerificationRun.status.in_(ACTIVE_STATUSES)
    ).first()
    if active:
        raise VerificationBlocked(f"Verification run {active.verification_run_id} is already active")
    baseline = db.query(func.count(BinanceExecutionAttempt.id)).filter(
        BinanceExecutionAttempt.mode == "BINANCE_LIVE",
        BinanceExecutionAttempt.is_test.is_(False),
    ).scalar() or 0
    row = LiveVerificationRun(
        verification_run_id=str(uuid4()), started_at=_utcnow(), starting_balance=float(starting_balance),
        baseline_historical_attempt_count=int(baseline), status="prepared", live_execution_enabled=False,
        completed_trades_during_this_run=0,
        verification_policy={"max_attempts": MAX_ATTEMPTS, "max_completed_trades": MAX_COMPLETED_TRADES,
            "max_planned_loss_usdt": MAX_PLANNED_LOSS_USDT, "max_combined_realised_loss_usdt": MAX_REALISED_LOSS_USDT,
            "max_leverage": MAX_LEVERAGE, "max_margin_allocation_pct": MAX_MARGIN_ALLOCATION_PCT,
            "minimum_headroom_usdt": MIN_HEADROOM_USDT, "margin_mode": "isolated"},
        deployment_revision=deployment_revision, deployment_image_digest=deployment_image_digest,
        initial_exchange_state=initial_exchange_state, timestamp_sync_state=timestamp_sync_state,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def active_run(db: Session) -> LiveVerificationRun | None:
    return db.query(LiveVerificationRun).filter(
        LiveVerificationRun.status.in_(ACTIVE_STATUSES)
    ).order_by(LiveVerificationRun.started_at.desc()).first()


def set_live_execution(db: Session, run_id: str, enabled: bool) -> LiveVerificationRun:
    row = db.get(LiveVerificationRun, run_id)
    if not row or row.status not in ACTIVE_STATUSES:
        raise VerificationBlocked("Verification run is not active")
    if enabled:
        stop_if_limited(db, run_id)
        db.refresh(row)
        if row.status not in ACTIVE_STATUSES:
            raise VerificationBlocked(row.stop_reason or "Verification run stopped")
    row.live_execution_enabled = bool(enabled)
    db.commit()
    return row


def validate_attempt(db: Session) -> str:
    """Fail closed before pre-submit work without consuming an attempt."""
    row = active_run(db)
    if not row:
        raise VerificationBlocked("No active verification run")
    loss_limit = MAX_REALISED_LOSS_USDT
    if not row.live_execution_enabled or row.status not in ACTIVE_STATUSES:
        raise VerificationBlocked("Verification run is disabled")
    if row.attempts_during_this_run >= MAX_ATTEMPTS:
        stop_if_limited(db, row.verification_run_id)
        raise VerificationBlocked("Maximum four acknowledged attempts reached")
    if row.completed_trades_during_this_run >= MAX_COMPLETED_TRADES:
        stop_if_limited(db, row.verification_run_id)
        raise VerificationBlocked("Two trade lifecycles already completed")
    if row.consecutive_losses_during_this_run >= MAX_CONSECUTIVE_LOSSES:
        stop_if_limited(db, row.verification_run_id)
        raise VerificationBlocked("Maximum consecutive losses reached")
    if row.realised_loss_during_this_run >= loss_limit:
        stop_if_limited(db, row.verification_run_id)
        raise VerificationBlocked("Maximum realised loss reached")
    if _as_utc(row.started_at) < _utcnow() - MAX_RUN_DURATION:
        stop_run(db, row.verification_run_id, "maximum_verification_duration_reached")
        raise VerificationBlocked("Verification duration expired")
    return row.verification_run_id


def claim_attempt(db: Session, run_id: str) -> str:
    """Atomically count an attempt immediately before real submission.

    Callers must release the claim when Binance rejects or never acknowledges
    the entry request. A successful Binance order response keeps the count.
    """
    row = active_run(db)
    if not row or row.verification_run_id != run_id:
        raise VerificationBlocked("No active verification run")
    loss_limit = MAX_REALISED_LOSS_USDT
    result = db.execute(
        update(LiveVerificationRun)
        .where(
            LiveVerificationRun.verification_run_id == row.verification_run_id,
            LiveVerificationRun.live_execution_enabled.is_(True),
            LiveVerificationRun.status.in_(ACTIVE_STATUSES),
            LiveVerificationRun.attempts_during_this_run < MAX_ATTEMPTS,
            LiveVerificationRun.completed_trades_during_this_run < MAX_COMPLETED_TRADES,
            LiveVerificationRun.consecutive_losses_during_this_run < MAX_CONSECUTIVE_LOSSES,
            LiveVerificationRun.realised_loss_during_this_run < loss_limit,
            LiveVerificationRun.started_at >= _utcnow() - MAX_RUN_DURATION,
        )
        .values(
            attempts_during_this_run=LiveVerificationRun.attempts_during_this_run + 1,
            status="running", updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        stop_if_limited(db, row.verification_run_id)
        raise VerificationBlocked("Verification run is disabled or a run safety limit has been reached")
    db.commit()
    return row.verification_run_id


def release_unacknowledged_attempt(db: Session, run_id: str) -> None:
    """Undo a pre-submit reservation when Binance did not acknowledge it."""
    db.execute(
        update(LiveVerificationRun)
        .where(
            LiveVerificationRun.verification_run_id == run_id,
            LiveVerificationRun.attempts_during_this_run > 0,
            LiveVerificationRun.status.in_(ACTIVE_STATUSES),
        )
        .values(attempts_during_this_run=LiveVerificationRun.attempts_during_this_run - 1,
                updated_at=_utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()


def stop_run(db: Session, run_id: str, reason: str) -> LiveVerificationRun:
    row = db.get(LiveVerificationRun, run_id)
    if not row:
        raise ValueError("Unknown verification run")
    row.status = "stopped"
    row.stop_reason = reason
    row.live_execution_enabled = False
    row.ended_at = row.ended_at or _utcnow()
    control = db.get(TradingControl, 1)
    if control:
        control.execution_enabled = False
        control.execution_state = "stopped"
    db.commit()
    return row


def stop_if_limited(db: Session, run_id: str) -> LiveVerificationRun:
    row = db.get(LiveVerificationRun, run_id)
    if not row:
        raise ValueError("Unknown verification run")
    reason = None
    if row.completed_trades_during_this_run >= MAX_COMPLETED_TRADES:
        reason = "TWO_REAL_TRADE_LIFECYCLES_VERIFIED"
    elif row.attempts_during_this_run >= MAX_ATTEMPTS:
        reason = "maximum_four_attempts_reached"
    elif row.consecutive_losses_during_this_run >= MAX_CONSECUTIVE_LOSSES:
        reason = "maximum_two_consecutive_losses_reached"
    elif row.realised_loss_during_this_run >= MAX_REALISED_LOSS_USDT:
        reason = "maximum_realised_loss_reached"
    elif _utcnow() - row.started_at.replace(tzinfo=timezone.utc) >= MAX_RUN_DURATION:
        reason = "maximum_24_hour_duration_reached"
    if reason:
        return stop_run(db, run_id, reason)
    return row


def record_closed_trade(db: Session, run_id: str, *, net_realised_pnl: float) -> LiveVerificationRun:
    """Count only separately closed trades with exchange-derived P&L after fees."""
    row = db.get(LiveVerificationRun, run_id)
    if not row or row.status not in ACTIVE_STATUSES:
        raise VerificationBlocked("Verification run is not active")
    if net_realised_pnl > 0:
        row.successful_trades_during_this_run += 1
        row.consecutive_losses_during_this_run = 0
    else:
        row.consecutive_losses_during_this_run += 1
        row.realised_loss_during_this_run += abs(min(net_realised_pnl, 0.0))
    row.completed_trades_during_this_run += 1
    row.updated_at = _utcnow()
    db.commit()
    return stop_if_limited(db, run_id)


def mark_reconciled(db: Session, run_id: str) -> LiveVerificationRun:
    row = db.get(LiveVerificationRun, run_id)
    if not row:
        raise ValueError("Unknown verification run")
    row.last_reconciled_at = _utcnow()
    db.commit()
    return row


def run_counts(db: Session, run_id: str) -> dict:
    row = db.get(LiveVerificationRun, run_id)
    if not row:
        raise ValueError("Unknown verification run")
    tagged = db.query(func.count(BinanceExecutionAttempt.id)).filter(
        BinanceExecutionAttempt.verification_run_id == run_id
    ).scalar() or 0
    return {
        "baseline_historical_attempt_count": row.baseline_historical_attempt_count,
        "attempts_during_this_run": row.attempts_during_this_run,
        "tagged_attempt_rows": tagged,
    }
