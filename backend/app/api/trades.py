from fastapi import APIRouter
import httpx

router = APIRouter(prefix="/api/trades", tags=["trades"])
BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def recent_trades(symbol: str, limit: int = 30):
    symbol = symbol.upper()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/trades",
            params={"symbol": symbol, "limit": limit},
        )
        r.raise_for_status()

    return {
        "symbol": symbol,
        "trades": [
            {
                "id": x["id"],
                "price": float(x["price"]),
                "qty": float(x["qty"]),
                "time": x["time"],
                "side": "SELL" if x["isBuyerMaker"] else "BUY",
            }
            for x in r.json()
        ],
    }
