from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.stress import report as stress_report
from app.stress.scenarios import get_scenario, list_scenarios
from app.stress.simulator import run_all, run_scenario

router = APIRouter(prefix="/api/stress", tags=["stress"])


@router.get("/scenarios")
async def scenarios():
    return {"scenarios": list_scenarios()}


@router.post("/run")
async def run(scenario_id: str | None = None, db: Session = Depends(get_db)):
    if scenario_id:
        if get_scenario(scenario_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown scenario '{scenario_id}'")
        results = [await run_scenario(scenario_id, db)]
    else:
        results = await run_all(db)

    run_id = stress_report.save_run(results, db=db)
    passed = sum(1 for r in results if r["status"] == "PASSED")

    return {
        "ok": True,
        "run_id": run_id,
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "results": results,
    }


@router.get("/report")
async def report(db: Session = Depends(get_db)):
    return stress_report.latest_run(db=db)
