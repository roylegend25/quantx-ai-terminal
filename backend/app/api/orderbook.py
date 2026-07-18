import time

import httpx
from fastapi import APIRouter, HTTPException

from app.core.response_meta import market_meta

router = APIRouter(prefix="/api/orderbook", tags=["orderbook"])

BINANCE_FAPI = "https://fapi.binance.com"

@router.get("/{symbol}")
async def orderbook(symbol: str, limit: int = 20):
    symbol = symbol.upper()
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/depth",
                params={"symbol": symbol, "limit": limit},
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Binance order book: {exc}")

    latency_ms = (time.monotonic() - started) * 1000
    data = r.json()

    bids = [{"price": float(p), "qty": float(q)} for p, q in data["bids"]]
    asks = [{"price": float(p), "qty": float(q)} for p, q in data["asks"]]
    # Binance's futures depth response carries its own transaction/event time
    # (ms) - the real age of the book snapshot, distinct from when we fetched it.
    raw_ts = data.get("T") or data.get("E")

    return {
        "symbol": symbol,
        "bids": bids,
        "asks": asks,
        "spread": round(asks[0]["price"] - bids[0]["price"], 4) if bids and asks else None,
        **market_meta(
            source="binance_futures", source_type="exchange_rest",
            market_timestamp=(raw_ts / 1000) if raw_ts else None,
            latency_ms=latency_ms,
        ),
    }
