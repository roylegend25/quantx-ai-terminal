from fastapi import APIRouter

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
