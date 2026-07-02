from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import httpx

router = APIRouter()

BINANCE = "https://fapi.binance.com"

@router.websocket("/ws/market/{symbol}")
async def market_socket(ws: WebSocket, symbol: str):
    await ws.accept()

    symbol = symbol.upper()

    try:
        while True:
            async with httpx.AsyncClient(timeout=10) as client:

                ticker = await client.get(
                    f"{BINANCE}/fapi/v1/ticker/24hr",
                    params={"symbol": symbol},
                )

                funding = await client.get(
                    f"{BINANCE}/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                )

                oi = await client.get(
                    f"{BINANCE}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                )

            await ws.send_json({
                "ticker": ticker.json(),
                "funding": funding.json(),
                "oi": oi.json()
            })

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        return
