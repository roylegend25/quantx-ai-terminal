from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import contextlib
import httpx

router = APIRouter()
BINANCE = "https://fapi.binance.com"

# Public market data is shared per backend process.  One provider loop fans
# out to every visible dashboard instead of each browser opening three new
# one-second REST pollers against the exchange.
_subscribers: dict[str, set[asyncio.Queue]] = {}
_tasks: dict[str, asyncio.Task] = {}

async def _provider_loop(symbol: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            while _subscribers.get(symbol):
                try:
                    ticker, funding, oi = await asyncio.gather(
                        client.get(f"{BINANCE}/fapi/v1/ticker/24hr", params={"symbol": symbol}),
                        client.get(f"{BINANCE}/fapi/v1/premiumIndex", params={"symbol": symbol}),
                        client.get(f"{BINANCE}/fapi/v1/openInterest", params={"symbol": symbol}),
                    )
                    ticker.raise_for_status(); funding.raise_for_status(); oi.raise_for_status()
                    payload = {"ticker": ticker.json(), "funding": funding.json(), "oi": oi.json()}
                    for queue in tuple(_subscribers.get(symbol, ())):
                        if queue.full():
                            with contextlib.suppress(asyncio.QueueEmpty):
                                queue.get_nowait()
                        with contextlib.suppress(asyncio.QueueFull):
                            queue.put_nowait(payload)
                except Exception:
                    # Preserve subscribers and retry; clients retain the last
                    # chart state and expose RECONNECTING/CACHED status.
                    pass
                await asyncio.sleep(1)
    finally:
        _tasks.pop(symbol, None)

@router.websocket("/ws/market/{symbol}")
async def market_socket(ws: WebSocket, symbol: str):
    await ws.accept()
    symbol = symbol.upper()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    _subscribers.setdefault(symbol, set()).add(queue)
    if symbol not in _tasks or _tasks[symbol].done():
        _tasks[symbol] = asyncio.create_task(_provider_loop(symbol))
    try:
        while True:
            await ws.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        subscribers = _subscribers.get(symbol)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                _subscribers.pop(symbol, None)
                task = _tasks.get(symbol)
                if task:
                    task.cancel()
