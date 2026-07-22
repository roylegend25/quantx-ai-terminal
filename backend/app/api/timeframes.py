from fastapi import APIRouter, HTTPException

from app.intelligence import market_intelligence
from app.timeframes.multi_timeframe import evaluate_all

router = APIRouter(prefix="/api/timeframes", tags=["timeframes"])


@router.get("/{symbol}")
async def timeframes(symbol: str):
    symbol = symbol.upper()

    try:
        market_context = await market_intelligence.get_context(symbol)
    except Exception:
        market_context = None

    result = await evaluate_all(symbol, market_context)

    return {
        "symbol": symbol,
        **result,
    }


@router.get("/{symbol}/horizon")
async def trading_horizon(symbol: str):
    """RETIRED: Trading Horizon has been removed from the production
    decision/execution path (see app.decision_engine.execution_gate, the
    single-authoritative-decision replacement). Use
    GET /api/trading/pipeline/current for the current decision/execution
    pipeline instead. Returns 410 rather than silently disappearing."""
    raise HTTPException(status_code=410, detail={
        "code": "TRADING_HORIZON_REMOVED",
        "message": "Trading Horizon has been removed from production. Use GET /api/trading/pipeline/current instead.",
    })
