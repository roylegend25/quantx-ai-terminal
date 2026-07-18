import time

import httpx
from fastapi import APIRouter, HTTPException

from app.core.response_meta import market_meta

router = APIRouter(prefix="/api/trades", tags=["trades"])
BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def recent_trades(symbol: str, limit: int = 30):
    symbol = symbol.upper()
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/trades",
                params={"symbol": symbol, "limit": limit},
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Binance recent trades: {exc}")

    latency_ms = (time.monotonic() - started) * 1000
    rows = r.json()
    latest_ms = max((x["time"] for x in rows), default=None)

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
            for x in rows
        ],
        **market_meta(
            source="binance_futures", source_type="exchange_rest",
            market_timestamp=(latest_ms / 1000) if latest_ms is not None else None,
            latency_ms=latency_ms,
        ),
    }
