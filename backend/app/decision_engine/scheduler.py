import asyncio, logging
from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.db.session import SessionLocal
from app.decision_engine.indicator_performance import evaluate_recent_indicators
from app.decision_engine.resolver import resolve_recent_due, resolve_historical_backfill
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.active_drive_resolver")
RUNNING = False

# STATUS tracks the recent-priority queue and is kept under the original
# key shape (running/last_run/.../last_stats) for backward compatibility -
# app.api.analysis and resolver_status.catchup_progress both read it
# directly. STATUS_BACKFILL is the new, additive per-queue detail for the
# historical queue; queue_status() below exposes both plus derived health
# fields (oldest_pending_age surfaced elsewhere via resolver_status, which
# already computes it straight from the DB).
STATUS = {"running": False, "last_run": None, "last_batch_at": None, "last_success": None,
          "last_resolved": 0, "last_error": None, "next_run": None, "last_stats": None}
STATUS_BACKFILL = {"running": False, "last_run": None, "last_success": None,
                    "last_resolved": 0, "last_error": None, "next_run": None, "last_stats": None}
STATUS_INDICATOR_PERFORMANCE = {"running": False, "last_run": None, "last_success": None,
                                 "last_evaluated": 0, "last_error": None, "next_run": None}


async def _recent_loop():
    global RUNNING
    while RUNNING:
        db = SessionLocal()
        now_iso = datetime.now(timezone.utc).isoformat()
        STATUS["last_run"] = now_iso
        STATUS["last_batch_at"] = now_iso
        try:
            stats = await resolve_recent_due(db, limit=settings.resolver_recent_batch_size,
                                             recent_window_hours=settings.resolver_recent_window_hours)
            count = stats["resolved"]
            STATUS.update(last_success=datetime.now(timezone.utc).isoformat(), last_resolved=count,
                          last_error=None, last_stats=stats)
            if count: log_event(logger, message="prediction_resolved", category="prediction", count=count, queue="recent")
        except Exception as exc:
            STATUS["last_error"] = repr(exc)
            log_event(logger, message="prediction_resolver_error", level=logging.ERROR, category="prediction", error=repr(exc), queue="recent")
        finally:
            db.close()
        STATUS["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=settings.resolver_recent_interval_seconds)).isoformat()
        await asyncio.sleep(settings.resolver_recent_interval_seconds)


async def _backfill_loop():
    global RUNNING
    while RUNNING:
        db = SessionLocal()
        now_iso = datetime.now(timezone.utc).isoformat()
        STATUS_BACKFILL["last_run"] = now_iso
        try:
            stats = await resolve_historical_backfill(db, limit=settings.resolver_backfill_batch_size,
                                                      recent_window_hours=settings.resolver_recent_window_hours)
            count = stats["resolved"]
            STATUS_BACKFILL.update(last_success=datetime.now(timezone.utc).isoformat(), last_resolved=count,
                                   last_error=None, last_stats=stats)
            if count: log_event(logger, message="prediction_resolved", category="prediction", count=count, queue="historical")
        except Exception as exc:
            STATUS_BACKFILL["last_error"] = repr(exc)
            log_event(logger, message="prediction_resolver_error", level=logging.ERROR, category="prediction", error=repr(exc), queue="historical")
        finally:
            db.close()
        STATUS_BACKFILL["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=settings.resolver_backfill_interval_seconds)).isoformat()
        await asyncio.sleep(settings.resolver_backfill_interval_seconds)


async def _indicator_performance_loop():
    global RUNNING
    while RUNNING:
        now_iso = datetime.now(timezone.utc).isoformat()
        STATUS_INDICATOR_PERFORMANCE["last_run"] = now_iso
        try:
            count = await asyncio.to_thread(
                evaluate_recent_indicators,
                lookback_hours=settings.indicator_performance_lookback_hours,
                batch_size=settings.indicator_performance_batch_size,
            )
            STATUS_INDICATOR_PERFORMANCE.update(
                last_success=datetime.now(timezone.utc).isoformat(), last_evaluated=count, last_error=None)
        except Exception as exc:
            STATUS_INDICATOR_PERFORMANCE["last_error"] = repr(exc)
            log_event(logger, message="indicator_performance_evaluator_error", level=logging.ERROR,
                      category="indicator_performance", error=repr(exc))
        STATUS_INDICATOR_PERFORMANCE["next_run"] = (
            datetime.now(timezone.utc) + timedelta(seconds=settings.indicator_performance_interval_seconds)
        ).isoformat()
        await asyncio.sleep(settings.indicator_performance_interval_seconds)


def start_scheduler():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    STATUS["running"] = True
    STATUS_BACKFILL["running"] = True
    STATUS_INDICATOR_PERFORMANCE["running"] = True
    asyncio.create_task(_recent_loop())
    asyncio.create_task(_backfill_loop())
    asyncio.create_task(_indicator_performance_loop())


def stop_scheduler():
    global RUNNING
    RUNNING = False
    STATUS["running"] = False
    STATUS_BACKFILL["running"] = False
    STATUS_INDICATOR_PERFORMANCE["running"] = False


def status():
    """Backward-compatible shape - the recent-priority queue's status,
    exactly as before the two-queue split."""
    return dict(STATUS)


def queue_status():
    """Full per-queue detail: {"recent": {...}, "historical": {...}, "indicator_performance": {...}}."""
    return {"recent": dict(STATUS), "historical": dict(STATUS_BACKFILL),
            "indicator_performance": dict(STATUS_INDICATOR_PERFORMANCE)}
