import asyncio, logging
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal
from app.decision_engine.resolver import resolve_due
from app.monitoring.logging import get_logger, log_event
logger = get_logger("quantx.active_drive_resolver")
RUNNING = False
STATUS = {"running": False, "last_run": None, "last_success": None, "last_resolved": 0, "last_error": None, "next_run": None}
async def _loop():
    global RUNNING
    while RUNNING:
        db = SessionLocal()
        STATUS["last_run"] = datetime.now(timezone.utc).isoformat()
        try:
            count = resolve_due(db)
            STATUS.update(last_success=datetime.now(timezone.utc).isoformat(), last_resolved=count, last_error=None)
            if count: log_event(logger, message="prediction_resolved", category="prediction", count=count)
        except Exception as exc:
            STATUS["last_error"] = repr(exc)
            log_event(logger, message="prediction_resolver_error", level=logging.ERROR, category="prediction", error=repr(exc))
        finally: db.close()
        STATUS["next_run"] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        await asyncio.sleep(60)
def start_scheduler():
    global RUNNING
    if RUNNING: return
    RUNNING = True
    STATUS["running"] = True
    asyncio.create_task(_loop())

def status():
    return dict(STATUS)
