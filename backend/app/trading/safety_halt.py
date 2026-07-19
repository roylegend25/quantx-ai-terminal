"""Fail-closed transitions shared by live background workers."""

from app.db.session import SessionLocal
from app.deployment import maintenance
from app.trading import modes, verification_runs


def halt_active_verification(reason: str) -> str | None:
    """Stop entries atomically while leaving live read reconciliation available."""
    db = SessionLocal()
    try:
        run = verification_runs.active_run(db)
        if run:
            run_id = run.verification_run_id
            verification_runs.stop_run(db, run_id, reason)
        else:
            run_id = None
            modes.set_execution_state("stopped", db=db)
        maintenance.enable(reason)
        return run_id
    finally:
        db.close()
