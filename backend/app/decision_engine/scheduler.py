import asyncio, logging
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal
from app.decision_engine.resolver import backfill_overdue_candles, resolve_due
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.active_drive_resolver")
RUNNING = False
CYCLE_SECONDS = 60
STATUS = {
    "running": False, "last_run": None, "last_batch_at": None, "last_success": None,
    "last_resolved": 0, "last_error": None, "next_run": None,
}


async def _loop():
    global RUNNING
    while RUNNING:
        db = SessionLocal()
        now_iso = datetime.now(timezone.utc).isoformat()
        STATUS["last_run"] = now_iso
        STATUS["last_batch_at"] = now_iso
        try:
            backfilled = await backfill_overdue_candles(db)
            count = resolve_due(db)
            STATUS.update(last_success=datetime.now(timezone.utc).isoformat(), last_resolved=count, last_error=None)
            if count or backfilled:
                log_event(logger, message="prediction_resolved", category="prediction", count=count, backfilled=backfilled)
        except Exception as exc:
            STATUS["last_error"] = repr(exc)
            log_event(logger, message="prediction_resolver_error", level=logging.ERROR, category="prediction", error=repr(exc))
        finally:
            db.close()
        STATUS["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=CYCLE_SECONDS)).isoformat()
        await asyncio.sleep(CYCLE_SECONDS)


def start_scheduler():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    STATUS["running"] = True
    asyncio.create_task(_loop())


def status():
    return dict(STATUS)
