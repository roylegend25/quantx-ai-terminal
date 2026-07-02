from fastapi import APIRouter
import httpx
from app.quant.indicators import compute_features

router = APIRouter(prefix="/api/quant", tags=["quant"])

BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def quant_features(symbol: str, interval: str = "5m", limit: int = 220):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        r.raise_for_status()

    candles = [
        {
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in r.json()
    ]

    features = compute_features(candles)

    return {
        "symbol": symbol,
        "interval": interval,
        "features": features["symbol_features"],
    }
