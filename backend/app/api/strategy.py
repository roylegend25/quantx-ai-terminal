from fastapi import APIRouter

from app.strategy.performance_repository import repository as performance_repository

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/weights")
async def strategy_weights():
    return performance_repository.get_all()
