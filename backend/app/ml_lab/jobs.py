"""Asynchronous training/HPO job management.

Jobs run in a separate OS process (`python -m app.ml_lab.runner <job_id>`)
so heavy fitting - including TensorFlow - can never block the FastAPI
event loop or bloat the API process. All state lives in the
ml_training_jobs table: the worker updates its own row's `progress` JSON as
it goes, the API just reads rows, and cancel is a SIGTERM to the recorded
pid. If the backend restarts mid-job, the orphaned 'running' row is reaped
to 'failed' the next time anything reads it (dead pid), so the UI never
shows a zombie forever.

Concurrency is capped at one running job: this box has 2 vCPUs shared with
live trading, and two concurrent fits would starve the prediction path.
Additional submissions queue and are started by whoever observes a free
slot next (submission or any jobs read).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import MLTrainingJob
from app.db.session import SessionLocal
from app.ml_lab.algorithms import ALGORITHMS, availability

MAX_RUNNING = 1
TERMINAL = ("succeeded", "failed", "cancelled")


def _row_to_dict(row: MLTrainingJob) -> dict:
    return {
        "job_id": row.job_id,
        "kind": row.kind,
        "algorithm": row.algorithm,
        "model_name": row.model_name,
        "params": row.params,
        "status": row.status,
        "progress": row.progress,
        "result_model_id": row.result_model_id,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # PermissionError means the pid exists but isn't ours - in this
        # single-user container that means it was recycled by another proc
        return False


def _reap_orphans(db: Session):
    """Any 'running' row whose worker process no longer exists died without
    reporting (OOM kill, container restart). Mark it failed with the honest
    reason instead of leaving it running forever."""
    rows = db.query(MLTrainingJob).filter(MLTrainingJob.status == "running").all()
    changed = False
    for row in rows:
        if not _pid_alive(row.pid):
            row.status = "failed"
            row.error = (
                "Worker process disappeared without reporting a result "
                "(likely killed by the OS for memory pressure, or a backend restart)"
            )
            row.finished_at = datetime.now(timezone.utc)
            changed = True
    if changed:
        db.commit()


def _spawn(row: MLTrainingJob, db: Session):
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.ml_lab.runner", row.job_id],
        cwd="/app",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # own process group: SIGTERM hits only the worker
    )
    row.status = "running"
    row.pid = proc.pid
    row.started_at = datetime.now(timezone.utc)
    db.commit()


def _start_queued_if_free(db: Session):
    running = db.query(MLTrainingJob).filter(MLTrainingJob.status == "running").count()
    if running >= MAX_RUNNING:
        return
    next_job = (
        db.query(MLTrainingJob)
        .filter(MLTrainingJob.status == "queued")
        .order_by(MLTrainingJob.created_at.asc())
        .first()
    )
    if next_job is not None:
        _spawn(next_job, db)


def submit(
    *,
    algorithm: str,
    kind: str = "train",
    model_name: str | None = None,
    params: dict | None = None,
    db: Session | None = None,
) -> dict:
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unknown algorithm '{algorithm}'. Valid: {list(ALGORITHMS)}")
    ok, reason = availability(algorithm)
    if not ok:
        raise RuntimeError(f"'{algorithm}' cannot train on this host: {reason}")

    owns_session = db is None
    db = db or SessionLocal()
    try:
        _reap_orphans(db)
        row = MLTrainingJob(
            job_id=uuid.uuid4().hex[:12],
            kind=kind,
            algorithm=algorithm,
            model_name=model_name or algorithm,
            params=params or {},
            status="queued",
            progress={"stage": "queued", "pct": 0, "message": "Waiting for a free training slot"},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _start_queued_if_free(db)
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def get(job_id: str, db: Session | None = None) -> dict | None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        _reap_orphans(db)
        _start_queued_if_free(db)
        row = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        return _row_to_dict(row) if row else None
    finally:
        if owns_session:
            db.close()


def list_jobs(status: str | None = None, limit: int = 50, db: Session | None = None) -> list[dict]:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        _reap_orphans(db)
        _start_queued_if_free(db)
        query = db.query(MLTrainingJob)
        if status:
            query = query.filter(MLTrainingJob.status == status)
        rows = query.order_by(MLTrainingJob.created_at.desc()).limit(limit).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        if owns_session:
            db.close()


def cancel(job_id: str, db: Session | None = None) -> dict | None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        if row is None:
            return None
        if row.status in TERMINAL:
            return _row_to_dict(row)

        if row.status == "running" and _pid_alive(row.pid):
            try:
                os.killpg(os.getpgid(row.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        row.status = "cancelled"
        row.error = "Cancelled by user"
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        _start_queued_if_free(db)
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()
