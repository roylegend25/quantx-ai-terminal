from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.mlops import drift_detector, retrainer, rollback as rollback_module
from app.mlops.experiment import tracker as experiment_tracker
from app.mlops.model_manager import maybe_promote
from app.mlops.model_registry import registry as model_registry

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(status: str | None = None, db: Session = Depends(get_db)):
    return {"models": model_registry.list_all(status=status, db=db)}


@router.get("/champion")
async def champion(model_name: str | None = None, db: Session = Depends(get_db)):
    return {"champion": model_registry.get_champion(model_name=model_name, db=db)}


@router.get("/history")
async def history(model_name: str, db: Session = Depends(get_db)):
    return {"model_name": model_name, "versions": model_registry.list_versions(model_name, db=db)}


@router.get("/experiments")
async def experiments(model_name: str | None = None, db: Session = Depends(get_db)):
    return {"experiments": experiment_tracker.list_all(model_name=model_name, db=db)}


@router.get("/drift")
async def drift(db: Session = Depends(get_db)):
    return {"drift": drift_detector.latest_by_type(db=db)}


@router.post("/train")
async def train(
    model_name: str = Body(..., embed=True),
    algorithm: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    result = retrainer.retrain(model_name, algorithm=algorithm, reason="manual", db=db)
    if result.get("status") == "failed":
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/promote/{model_id}")
async def promote(model_id: str, db: Session = Depends(get_db)):
    model = model_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = maybe_promote(model_id, db=db)
    if not result["promoted"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/rollback/{model_id}")
async def rollback(model_id: str, db: Session = Depends(get_db)):
    model = model_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    try:
        return rollback_module.rollback(model["model_name"], db=db)
    except rollback_module.NoPreviousChampionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/archive/{model_id}")
async def archive(model_id: str, db: Session = Depends(get_db)):
    from app.mlops.model_registry import STATUS_ARCHIVED

    model = model_registry.set_status(model_id, STATUS_ARCHIVED, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return {"model": model}
