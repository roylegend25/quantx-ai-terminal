from fastapi import APIRouter
import httpx

router = APIRouter(prefix="/api/market", tags=["market"])

BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def get_market(symbol: str):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=15) as client:
        ticker = await client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol})
        funding = await client.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
        oi = await client.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})

        ticker.raise_for_status()
        funding.raise_for_status()
        oi.raise_for_status()

    return {
        "symbol": symbol,
        "ticker": ticker.json(),
        "funding": funding.json(),
        "open_interest": oi.json(),
    }

@router.get("/{symbol}/candles")
async def get_candles(symbol: str, interval: str = "5m", limit: int = 220):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        r.raise_for_status()

    return [
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
