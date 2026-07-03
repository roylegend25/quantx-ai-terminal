from fastapi import APIRouter

from app.execution import execution_metrics as metrics
from app.execution.execution_engine import engine

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/status")
async def status():
    return engine.status()


@router.get("/metrics")
async def execution_metrics_endpoint():
    return {
        **metrics.summary(),
        "recent_executions": metrics.recent(20),
    }
