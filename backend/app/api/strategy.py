from fastapi import APIRouter

from app.strategy import weighting

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/weights")
async def strategy_weights():
    return weighting.get_stats()
