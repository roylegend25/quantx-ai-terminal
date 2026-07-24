"""Guarded raw-candle retention/cleanup (main-purpose consolidation, Stage 2).

market_candles is the bot's own transient ingestion cache, not the
permanent record - predictions, resolutions, contributor records, trades,
and daily performance snapshots are never touched here and are never
deleted as a side effect of this module. A candle is only ever eligible for
deletion once it can no longer affect anything still open:

  1. It is complete (only ever true for candles well in the past - the
     currently-forming candle for any timeframe is always inside every
     buffer below).
  2/3/10. No PredictionLedger row for its (symbol, timeframe) that is still
     in a non-terminal lifecycle_status (PENDING/RESOLVING/
     RESOLUTION_ERROR_RETRYING) could still need it - enforced by refusing
     to delete anything at or after (oldest open prediction's
     resolution_deadline - a safety margin), not just "this candle is old".
  4/5/6/7. Contributor performance + a daily snapshot (with its fingerprint)
     already exists for every UTC date the candle's date could contribute
     to - enforced by requiring a DailyV2Performance row for that
     (symbol, timeframe, date) before that date's candles become eligible.
  9. No open (non-terminal) trade/position depends on it - a currently
     forming candle is always inside the rolling buffer below, and a
     resolver dependency is covered by #2/3/10 above.

On top of all of that, a fixed rolling buffer (current UTC day + the
previous completed day, minimum) is never eligible regardless of the above,
and long-horizon timeframes (1w/1M) get a much longer minimum derived from
their own resolution horizon (app.decision_engine.ledger.HORIZON_SECONDS) -
a 1M prediction can still be open a month after its candles were fetched.

dry_run_report() never modifies anything. run_cleanup() requires an
explicit confirm=True and re-derives eligibility fresh at delete time
(never trusts a stale dry-run snapshot) - see MIGRATIONS.md-style caution:
back up before the first real run.
"""
from __future__ import annotations

import math
from datetime import date as date_type, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ActiveDriveDecision, DailyV2Performance, MarketCandle, PredictionLedger
from app.db.session import SessionLocal
from app.decision_engine.ledger import HORIZON_SECONDS
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.analytics.candle_retention")

# Always retained regardless of anything else - "retain the current UTC day,
# retain the previous completed day" from the task spec.
ROLLING_BUFFER_DAYS = 2

# Extra slack beyond a timeframe's own resolution horizon / a still-open
# prediction's deadline, to absorb resolver retry/backfill latency rather
# than racing it.
SAFETY_MARGIN_DAYS = 3

_NON_TERMINAL_STATUSES = ("PENDING", "RESOLVING", "RESOLUTION_ERROR_RETRYING")


def _timeframe_min_retention_days(timeframe: str) -> int:
    if timeframe == "1M":
        return 32 + SAFETY_MARGIN_DAYS  # calendar month + slack
    horizon_seconds = HORIZON_SECONDS.get(timeframe, 1500)
    return max(ROLLING_BUFFER_DAYS, math.ceil(horizon_seconds / 86400) + SAFETY_MARGIN_DAYS)


def _oldest_open_deadline(db: Session, symbol: str, timeframe: str) -> datetime | None:
    row = (
        db.query(func.min(PredictionLedger.resolution_deadline))
        .filter(
            PredictionLedger.symbol == symbol, PredictionLedger.timeframe == timeframe,
            PredictionLedger.lifecycle_status.in_(_NON_TERMINAL_STATUSES),
        )
        .scalar()
    )
    return row


def _daily_snapshot_exists(db: Session, symbol: str, timeframe: str, day: date_type) -> bool:
    return db.query(DailyV2Performance.id).filter(
        DailyV2Performance.symbol == symbol, DailyV2Performance.timeframe == timeframe,
        DailyV2Performance.date == day,
    ).first() is not None


def _cutoff_for(db: Session, symbol: str, timeframe: str, now: datetime) -> tuple[datetime, list[str]]:
    """Returns (cutoff, reasons) - a candle at or after `cutoff` is
    protected; the earliest-protecting (most conservative) constraint wins.
    `reasons` documents which constraint(s) actually bind, for the dry-run
    report."""
    reasons = []
    rolling_cutoff = now - timedelta(days=ROLLING_BUFFER_DAYS)
    candidates = [(rolling_cutoff, "within_rolling_buffer")]

    tf_cutoff = now - timedelta(days=_timeframe_min_retention_days(timeframe))
    candidates.append((tf_cutoff, "within_timeframe_min_retention"))

    open_deadline = _oldest_open_deadline(db, symbol, timeframe)
    if open_deadline is not None:
        if open_deadline.tzinfo is None:
            open_deadline = open_deadline.replace(tzinfo=timezone.utc)
        open_cutoff = open_deadline - timedelta(days=SAFETY_MARGIN_DAYS)
        candidates.append((open_cutoff, "unresolved_prediction_depends_on_it"))

    cutoff = min(c for c, _ in candidates)
    reasons = [reason for c, reason in candidates if c == cutoff]
    return cutoff, reasons


def _missing_snapshot_dates(db: Session, symbol: str, timeframe: str, oldest_candle_date: date_type, cutoff_date: date_type) -> list[date_type]:
    """Every UTC date strictly before cutoff_date that has predictions for
    this (symbol, timeframe) but no daily snapshot yet - those dates'
    candles stay protected even if otherwise old enough."""
    dates_with_predictions = {
        d for (d,) in db.query(func.date(PredictionLedger.generated_at)).filter(
            PredictionLedger.symbol == symbol, PredictionLedger.timeframe == timeframe,
            PredictionLedger.generated_at >= datetime(oldest_candle_date.year, oldest_candle_date.month, oldest_candle_date.day, tzinfo=timezone.utc),
            PredictionLedger.generated_at < datetime(cutoff_date.year, cutoff_date.month, cutoff_date.day, tzinfo=timezone.utc),
        ).distinct().all()
    }
    missing = []
    for d in dates_with_predictions:
        day = d if isinstance(d, date_type) else datetime.strptime(d, "%Y-%m-%d").date()
        if not _daily_snapshot_exists(db, symbol, timeframe, day):
            missing.append(day)
    return sorted(missing)


def _scope_report(db: Session, symbol: str, timeframe: str, now: datetime) -> dict:
    cutoff, reasons = _cutoff_for(db, symbol, timeframe, now)
    cutoff_date = cutoff.date()

    oldest_row = db.query(func.min(MarketCandle.timestamp)).filter(
        MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
    ).scalar()
    if oldest_row is None:
        return {
            "symbol": symbol, "timeframe": timeframe, "eligible_rows": 0, "protected_rows": 0,
            "protection_reasons": {}, "oldest_protected_timestamp": None,
        }
    oldest_candle_date = datetime.fromtimestamp(oldest_row / 1000, tz=timezone.utc).date()

    missing_dates = _missing_snapshot_dates(db, symbol, timeframe, oldest_candle_date, cutoff_date)
    if missing_dates:
        # The earliest missing-snapshot date becomes the effective cutoff -
        # nothing at or after it may be deleted until that date's snapshot
        # exists, regardless of how old it otherwise looks.
        effective_cutoff_ms = int(datetime(missing_dates[0].year, missing_dates[0].month, missing_dates[0].day, tzinfo=timezone.utc).timestamp() * 1000)
        reasons = reasons + ["daily_snapshot_missing"]
    else:
        effective_cutoff_ms = int(cutoff.timestamp() * 1000)

    total = db.query(func.count(MarketCandle.id)).filter(
        MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
    ).scalar() or 0
    eligible = db.query(func.count(MarketCandle.id)).filter(
        MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
        MarketCandle.timestamp < effective_cutoff_ms,
    ).scalar() or 0
    protected = total - eligible

    oldest_protected_row = db.query(func.min(MarketCandle.timestamp)).filter(
        MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
        MarketCandle.timestamp >= effective_cutoff_ms,
    ).scalar()
    oldest_protected_ts = (
        datetime.fromtimestamp(oldest_protected_row / 1000, tz=timezone.utc).isoformat()
        if oldest_protected_row is not None else None
    )

    protection_reasons = {r: True for r in reasons} if protected else {}

    return {
        "symbol": symbol, "timeframe": timeframe,
        "eligible_rows": eligible, "protected_rows": protected,
        "protection_reasons": protection_reasons,
        "oldest_protected_timestamp": oldest_protected_ts,
        "effective_cutoff_ms": effective_cutoff_ms,
    }


def dry_run_report(db: Session | None = None) -> dict:
    """Read-only. Never modifies anything. Reports, per (symbol, timeframe):
    rows eligible for deletion, rows protected and why, the oldest
    protected timestamp, and an estimate of disk space recovered."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        symbols = settings.symbols
        timeframes = sorted({tf for (tf,) in db.query(MarketCandle.timeframe).distinct().all()})

        scopes = [_scope_report(db, symbol, tf, now) for symbol in symbols for tf in timeframes]
        total_eligible = sum(s["eligible_rows"] for s in scopes)
        total_protected = sum(s["protected_rows"] for s in scopes)

        # Rough average-row-size estimate from SQLite's own page accounting
        # (dbstat), rather than assuming a fixed row size.
        avg_row_bytes = 200  # conservative fallback estimate for a MarketCandle row
        try:
            from sqlalchemy import text
            table_bytes = db.execute(text("SELECT SUM(pgsize) FROM dbstat WHERE name='market_candles'")).scalar()
            if table_bytes and (total_eligible + total_protected):
                avg_row_bytes = table_bytes / max(1, total_eligible + total_protected)
        except Exception:
            pass

        return {
            "generated_at": now.isoformat(),
            "scopes": scopes,
            "total_eligible_rows": total_eligible,
            "total_protected_rows": total_protected,
            "estimated_bytes_recoverable": round(total_eligible * avg_row_bytes),
            "confirmation_required": True,
            "note": "Dry run only - no rows were modified. Call run_cleanup(confirm=True) to actually delete.",
        }
    finally:
        if owns_session:
            db.close()


def run_cleanup(confirm: bool, db: Session | None = None) -> dict:
    """Deletes only rows that are eligible at the moment of the call
    (eligibility is re-derived here, not taken from a prior dry run).
    Refuses to run at all without confirm=True. Every batch is written to
    the trading audit log (app.trading.modes.audit) before returning."""
    if not confirm:
        raise ValueError("run_cleanup requires confirm=True - this is a destructive operation on market_candles")

    owns_session = db is None
    db = db or SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        symbols = settings.symbols
        timeframes = sorted({tf for (tf,) in db.query(MarketCandle.timeframe).distinct().all()})

        deleted_by_scope = []
        total_deleted = 0
        for symbol in symbols:
            for timeframe in timeframes:
                scope = _scope_report(db, symbol, timeframe, now)
                if scope["eligible_rows"] == 0:
                    continue
                cutoff_ms = scope["effective_cutoff_ms"]
                deleted = (
                    db.query(MarketCandle)
                    .filter(
                        MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
                        MarketCandle.timestamp < cutoff_ms,
                    )
                    .delete(synchronize_session=False)
                )
                if deleted:
                    deleted_by_scope.append({"symbol": symbol, "timeframe": timeframe, "deleted": deleted})
                    total_deleted += deleted
        db.commit()

        from app.trading import modes
        modes.audit(
            "candle_cleanup_batch", detail={
                "total_deleted": total_deleted, "scopes": deleted_by_scope,
                "rolling_buffer_days": ROLLING_BUFFER_DAYS, "safety_margin_days": SAFETY_MARGIN_DAYS,
            }, db=db,
        )
        log_event(
            logger, message="candle_cleanup_completed", category="analytics",
            total_deleted=total_deleted, scopes=len(deleted_by_scope),
        )
        return {"total_deleted": total_deleted, "scopes": deleted_by_scope, "ran_at": now.isoformat()}
    finally:
        if owns_session:
            db.close()
