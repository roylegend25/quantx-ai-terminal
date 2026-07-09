"""Background scheduler for the ML lifecycle platform: drift scan, model
health check, cleanup, and automatic challenger training - one cycle every
settings.mlops_scheduler_interval_seconds. Mirrors the
start_scheduler/stop_scheduler/RUNNING shape of app/trading/scheduler.py,
started alongside it from app/main.py's delayed_background_start, gated by
settings.auto_retrain.

Scheduled/drift-triggered training goes through the same pipeline the UI's
train-now button uses (app/ml_lab/jobs.py worker subprocesses): preflight
health gate -> queue one async job per configured+available algorithm ->
each job trains, backtests, walk-forward validates, and applies the
promotion rules. Nothing heavy ever runs on this event loop.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.models import MLOpsModel
from app.db.session import SessionLocal
from app.ml_lab import notifications, preflight
from app.ml_lab import jobs as lab_jobs
from app.ml_lab.algorithms import availability
from app.mlops import drift_detector, model_loader, retrainer
from app.mlops.model_registry import (
    STATUS_ARCHIVED,
    STATUS_CHAMPION,
    STATUS_FAILED,
    STATUS_TESTING,
    registry,
)
from app.monitoring.logging import get_logger, log_event

RUNNING = False
logger = get_logger("quantx.mlops_scheduler")

SCHEDULE_INTERVALS = {
    "every_6_hours": timedelta(hours=6),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}

# Minimum gap between drift-triggered retrain batches (see _run_cycle).
DRIFT_RETRAIN_COOLDOWN = timedelta(hours=6)
_last_drift_retrain: datetime | None = None


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


def _lab_retraining(db) -> dict:
    from app.ml_lab.settings_repo import get_settings as get_lab_settings

    try:
        return get_lab_settings(db=db)["retraining"]
    except Exception:
        return {"schedule": settings.mlops_retrain_schedule, "drift_trigger_enabled": True}


def run_scheduled_training(reason: str, algorithms: list[str] | None = None, db=None) -> dict:
    """The one entry point for every automatic/manual batch: preflight
    health gate first (skip + notify when unhealthy), then one async
    training job per configured algorithm that is actually available on
    this host. Shared by the scheduler cycle and POST /api/ml/train-now."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        gate = preflight.run_preflight(db=db)
        if not gate["healthy"]:
            quality_failures = [f for f in gate["failures"] if f.startswith("data_quality") or f.startswith("label_balance")]
            notifications.notify(
                notifications.EVENT_TRAINING_SKIPPED,
                title=f"Training skipped ({reason}): system not healthy",
                message="; ".join(gate["failures"][:5]),
                severity="warning",
                data={"reason": reason, "checks": gate["checks"]},
                db=db,
            )
            if quality_failures:
                notifications.notify(
                    notifications.EVENT_DATA_QUALITY_WARNING,
                    title="Data quality below training threshold",
                    message="; ".join(quality_failures[:5]),
                    severity="warning",
                    data={"failures": quality_failures},
                    db=db,
                )
            return {"started": False, "reason": "preflight_failed", "preflight": gate, "jobs": [], "skipped": []}

        retraining = _lab_retraining(db)
        wanted = algorithms or retraining.get("schedule_algorithms") or ["xgboost", "lightgbm", "catboost", "random_forest", "ensemble_ml"]

        submitted, skipped = [], []
        for algorithm in wanted:
            ok, why = availability(algorithm)
            if not ok:
                skipped.append({"algorithm": algorithm, "reason": why})
                continue
            try:
                submitted.append(
                    lab_jobs.submit(algorithm=algorithm, params={"dataset": "market_history", "trigger": reason}, db=db)
                )
            except (ValueError, RuntimeError) as exc:
                skipped.append({"algorithm": algorithm, "reason": str(exc)})

        _record_scheduled_run(db)
        return {"started": True, "preflight": gate, "jobs": submitted, "skipped": skipped}
    finally:
        if owns_session:
            db.close()


def _record_scheduled_run(db):
    from app.ml_lab.settings_repo import update_settings

    try:
        update_settings({"retraining": {"last_scheduled_run": datetime.now(timezone.utc).isoformat()}}, db=db)
    except Exception:
        pass


def _scheduled_due(retraining: dict) -> bool:
    schedule = retraining.get("schedule", "manual")
    if schedule == "manual" or schedule not in SCHEDULE_INTERVALS:
        return False
    last_run = retraining.get("last_scheduled_run")
    if not last_run:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= SCHEDULE_INTERVALS[schedule]


async def _run_cycle():
    global _last_drift_retrain

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

        drifted = {
            drift_type: record
            for drift_type, record in drift_detector.latest_by_type(db=db).items()
            if record and record.get("is_drifted")
        }
        if drifted:
            # deduped inside notify() - one warning per 6h window while the
            # condition persists, not one per scheduler cycle
            notifications.notify(
                notifications.EVENT_DRIFT_WARNING,
                title=f"Drift detected: {', '.join(sorted(drifted))}",
                message="; ".join(
                    f"{t} drift score {r.get('score')}" for t, r in sorted(drifted.items())
                ),
                severity="warning",
                data={t: {"score": r.get("score")} for t, r in drifted.items()},
                db=db,
            )

        retraining = _lab_retraining(db)

        if _scheduled_due(retraining):
            run_scheduled_training(reason="schedule", db=db)
        elif retraining.get("drift_trigger_enabled", True) and drifted:
            now = datetime.now(timezone.utc)
            cooled_down = _last_drift_retrain is None or (now - _last_drift_retrain) >= DRIFT_RETRAIN_COOLDOWN
            if cooled_down and retrainer.should_retrain(db=db)["due"]:
                run_scheduled_training(reason="drift", algorithms=retraining.get("drift_algorithms"), db=db)
                _last_drift_retrain = now
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
