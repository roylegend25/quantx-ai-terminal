"""Persists stress-test run results and serves the latest report.

One row per scenario per run (grouped by run_id) - purely an audit trail,
same pattern as app/research/experiment_tracker.py. Nothing here is read by
the live trading path.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import StressTestRun
from app.db.session import SessionLocal


def _row_to_dict(row: StressTestRun) -> dict:
    return {
        "scenario_id": row.scenario_id,
        "scenario_name": row.scenario_name,
        "category": row.category,
        "status": row.status,
        "reason": row.reason,
        "checks": row.checks,
        "new_trades_blocked": row.new_trades_blocked,
        "open_positions_protected": row.open_positions_protected,
        "positions_detail": row.positions_detail,
        "risk_result": row.risk_result,
        "duration_ms": row.duration_ms,
        "timestamp": row.created_at.isoformat() if row.created_at else None,
    }


def save_run(results: list[dict], db: Session | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    owns_session = db is None
    db = db or SessionLocal()
    try:
        created_at = datetime.now(timezone.utc)
        for result in results:
            db.add(StressTestRun(
                run_id=run_id,
                scenario_id=result["scenario_id"],
                scenario_name=result["scenario_name"],
                category=result.get("category"),
                status=result["status"],
                reason=result["reason"],
                checks=result["checks"],
                new_trades_blocked=result["new_trades_blocked"],
                open_positions_protected=result["open_positions_protected"],
                positions_detail=result.get("positions_detail"),
                risk_result=result["risk_result"],
                duration_ms=result["duration_ms"],
                created_at=created_at,
            ))
        db.commit()
        return run_id
    finally:
        if owns_session:
            db.close()


def latest_run(db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        latest = (
            db.query(StressTestRun.run_id)
            .order_by(StressTestRun.created_at.desc())
            .first()
        )
        if latest is None:
            return {
                "run_id": None,
                "generated_at": None,
                "summary": {"total": 0, "passed": 0, "failed": 0},
                "results": [],
            }

        run_id = latest[0]
        rows = (
            db.query(StressTestRun)
            .filter(StressTestRun.run_id == run_id)
            .order_by(StressTestRun.id.asc())
            .all()
        )
        results = [_row_to_dict(r) for r in rows]
        passed = sum(1 for r in results if r["status"] == "PASSED")

        return {
            "run_id": run_id,
            "generated_at": results[0]["timestamp"] if results else None,
            "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
            "results": results,
        }
    finally:
        if owns_session:
            db.close()
