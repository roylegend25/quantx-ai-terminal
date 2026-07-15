import asyncio, logging
from app.db.session import SessionLocal
from app.decision_engine.resolver import resolve_due
from app.monitoring.logging import get_logger, log_event
logger = get_logger("quantx.active_drive_resolver")
RUNNING = False
async def _loop():
    global RUNNING
    while RUNNING:
        db = SessionLocal()
        try:
            count = resolve_due(db)
            if count: log_event(logger, message="prediction_resolved", category="prediction", count=count)
        except Exception as exc:
            log_event(logger, message="prediction_resolver_error", level=logging.ERROR, category="prediction", error=repr(exc))
        finally: db.close()
        await asyncio.sleep(60)
def start_scheduler():
    global RUNNING
    if RUNNING: return
    RUNNING = True
    asyncio.create_task(_loop())
