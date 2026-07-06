"""Phase 20 auto-learning API. Everything here is advisory/research: the
evaluation snapshots, candidate weights and recommendations never mutate the
live prediction path, and challenger training goes through the existing
Challenger-only job runner."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.learning import evaluator, recommender

router = APIRouter(prefix="/api/learning", tags=["learning"])


class EvaluateRequest(BaseModel):
    symbol: str | None = None
    limit: int = 2000


class TrainChallengerRequest(BaseModel):
    algorithm: str = "lightgbm"
    model_name: str | None = None
    params: dict | None = None


@router.post("/evaluate")
async def evaluate(req: EvaluateRequest | None = None, db: Session = Depends(get_db)):
    req = req or EvaluateRequest()
    return {"ok": True, "performance": evaluator.evaluate(symbol=req.symbol, limit=req.limit, db=db)}


@router.get("/performance")
async def performance(db: Session = Depends(get_db)):
    return {"performance": evaluator.latest_performance(db=db)}


@router.get("/strategy-weights")
async def strategy_weights(db: Session = Depends(get_db)):
    return recommender.strategy_weights(db=db)


@router.post("/train-challenger")
async def train_challenger(req: TrainChallengerRequest | None = None):
    req = req or TrainChallengerRequest()
    try:
        job = recommender.train_challenger(algorithm=req.algorithm, model_name=req.model_name, params=req.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "job": job, "note": "Challenger only - promotion still requires the gated promote endpoint"}


@router.get("/recommendations")
async def recommendations(db: Session = Depends(get_db)):
    return recommender.recommendations(db=db)
