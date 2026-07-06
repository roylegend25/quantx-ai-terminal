"""Background scheduler for the ML lifecycle platform: drift scan, retrain
check, model health check, cleanup - one cycle every
settings.mlops_scheduler_interval_seconds. Mirrors the
start_scheduler/stop_scheduler/RUNNING shape of app/trading/scheduler.py,
started alongside it from app/main.py's delayed_background_start, gated by
settings.auto_retrain.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.models import MLOpsModel
from app.db.session import SessionLocal
from app.mlops import drift_detector, model_loader, retrainer
from app.mlops.model_registry import (
    STATUS_ARCHIVED,
    STATUS_CHAMPION,
    STATUS_FAILED,
    STATUS_TESTING,
    registry,
)
from app.mlops.retrainer import ALGORITHMS
from app.monitoring.logging import get_logger, log_event

RUNNING = False
_last_retrain_check: datetime | None = None
logger = get_logger("quantx.mlops_scheduler")

SCHEDULE_INTERVALS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}

# Minimum gap between drift-triggered retrain batches (see _retrain_due).
DRIFT_RETRAIN_COOLDOWN = timedelta(hours=6)


def health_check(db=None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        champions = registry.list_all(status=STATUS_CHAMPION, db=db)
        report = {}
        for champion in champions:
            model_path = champion.get("model_path")
            report[champion["model_name"]] = {
                "model_id": champion["model_id"],
                "version": champion["version"],
                "artifact_available": model_loader.is_available(model_path) if model_path else True,
            }
        return report
    finally:
        if owns_session:
            db.close()


def cleanup(db=None) -> dict:
    """Archives stale rows past MODEL_RETENTION_DAYS. Never deletes a row
    or artifact - MAX_MODEL_HISTORY/MODEL_RETENTION_DAYS only control what
    gets archived out of the active Testing/Challenger pool, per "never
    delete models" in the spec."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.model_retention_days)
        stale = (
            db.query(MLOpsModel)
            .filter(MLOpsModel.status.in_([STATUS_TESTING, STATUS_FAILED]))
            .filter(MLOpsModel.trained_at < cutoff)
            .all()
        )
        for row in stale:
            row.status = STATUS_ARCHIVED
        db.commit()
        return {"archived": len(stale)}
    finally:
        if owns_session:
            db.close()


async def _retrain_due(db) -> bool:
    global _last_retrain_check

    # Dashboard-editable schedule (app/ml_lab/settings_repo.py) takes
    # precedence over the static env default; "manual" disables scheduled
    # retraining entirely while leaving the drift trigger configurable.
    try:
        from app.ml_lab.settings_repo import get_settings as get_lab_settings

        lab = get_lab_settings(db=db)["retraining"]
        schedule = lab.get("schedule", settings.mlops_retrain_schedule)
        drift_trigger_enabled = bool(lab.get("drift_trigger_enabled", True))
    except Exception:
        schedule = settings.mlops_retrain_schedule
        drift_trigger_enabled = True

    now = datetime.now(timezone.utc)
    if schedule == "manual":
        scheduled_due = False
    else:
        interval = SCHEDULE_INTERVALS.get(schedule, SCHEDULE_INTERVALS["daily"])
        scheduled_due = _last_retrain_check is None or (now - _last_retrain_check) >= interval

    # Drift stays elevated for hours once it crosses a threshold - without a
    # cooldown the trigger fires on EVERY cycle and registers a new
    # challenger batch each time (observed: v1..v8 sprawl within one
    # evening). One drift-triggered batch per cooldown window is enough;
    # the drift doesn't get more drifted by retraining on it again.
    trigger_due = False
    if drift_trigger_enabled:
        cooled_down = _last_retrain_check is None or (now - _last_retrain_check) >= DRIFT_RETRAIN_COOLDOWN
        if cooled_down:
            trigger_due = retrainer.should_retrain(db=db)["due"]
    return scheduled_due or trigger_due


async def _run_cycle():
    global _last_retrain_check

    db = SessionLocal()
    try:
        # extended scan = the original feature/prediction/market checks plus
        # data/concept drift and KL/Wasserstein readings (app/ml_lab/drift_ext)
        try:
            from app.ml_lab.drift_ext import run_extended_scan

            run_extended_scan(db=db)
        except Exception:
            drift_detector.run_full_scan(db=db)
        health_check(db=db)
        cleanup(db=db)

        if await _retrain_due(db):
            for algorithm in ALGORITHMS:
                try:
                    retrainer.retrain(algorithm, algorithm=algorithm, reason="schedule", db=db)
                except Exception as e:
                    log_event(
                        logger, message="mlops_retrain_error", level=logging.ERROR,
                        category="scheduler", strategy=algorithm, error=repr(e),
                    )
            _last_retrain_check = datetime.now(timezone.utc)
    except Exception as e:
        log_event(logger, message="mlops_scheduler_cycle_error", level=logging.ERROR, category="scheduler", error=repr(e))
    finally:
        db.close()


async def _loop():
    global RUNNING
    while RUNNING:
        await _run_cycle()
        await asyncio.sleep(settings.mlops_scheduler_interval_seconds)


def start_scheduler():
    global RUNNING
    if RUNNING or not settings.auto_retrain:
        return
    RUNNING = True
    asyncio.create_task(_loop())


def stop_scheduler():
    global RUNNING
    RUNNING = False
