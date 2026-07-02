from fastapi import APIRouter
import httpx

router = APIRouter(prefix="/api/orderbook", tags=["orderbook"])

BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def orderbook(symbol: str, limit: int = 20):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit},
        )
        r.raise_for_status()

    data = r.json()

    bids = [{"price": float(p), "qty": float(q)} for p, q in data["bids"]]
    asks = [{"price": float(p), "qty": float(q)} for p, q in data["asks"]]

    return {
        "symbol": symbol,
        "bids": bids,
        "asks": asks,
        "spread": round(asks[0]["price"] - bids[0]["price"], 4) if bids and asks else None,
    }
