import asyncio
import json

import websockets

BINANCE_WS = "wss://fstream.binance.com/ws"

DEFAULT_LISTEN_SECONDS = 1.5
CONNECT_TIMEOUT_SECONDS = 2.5
# hard ceiling on connect + listen combined, in case the handshake itself is
# slow - this collector must never dominate the overall context latency
HARD_BUDGET_SECONDS = 4.0


async def _listen(symbol: str, listen_seconds: float) -> dict:
    stream = f"{BINANCE_WS}/{symbol.lower()}@forceOrder"

    count = 0
    notional = 0.0
    long_liquidations = 0
    short_liquidations = 0

    async with websockets.connect(stream, open_timeout=CONNECT_TIMEOUT_SECONDS) as ws:
        deadline = asyncio.get_event_loop().time() + listen_seconds
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            event = json.loads(raw).get("o", {})
            side = event.get("S")
            price = float(event.get("ap") or event.get("p") or 0)
            qty = float(event.get("q") or 0)

            count += 1
            notional += price * qty
            if side == "SELL":
                long_liquidations += 1
            elif side == "BUY":
                short_liquidations += 1

    return {
        "count": count, "notional": notional,
        "long_liquidations": long_liquidations, "short_liquidations": short_liquidations,
    }


async def fetch(symbol: str, listen_seconds: float = DEFAULT_LISTEN_SECONDS) -> dict:
    """Listens briefly to Binance's forced-liquidation order stream.

    Binance only publishes liquidations in real time (no public REST history),
    so this samples a short window. A quiet window is expected and normal -
    forced liquidations for a single symbol are relatively rare events.

    SELL-side liquidations are forced-closed longs (bearish pressure).
    BUY-side liquidations are forced-closed shorts (bullish pressure).
    """
    count = notional = long_liquidations = short_liquidations = 0

    try:
        result = await asyncio.wait_for(_listen(symbol, listen_seconds), timeout=HARD_BUDGET_SECONDS)
        count = result["count"]
        notional = result["notional"]
        long_liquidations = result["long_liquidations"]
        short_liquidations = result["short_liquidations"]
    except Exception:
        # slow/unavailable handshake or network hiccup - report a quiet
        # window rather than blocking or failing the whole context request
        pass

    if long_liquidations > short_liquidations:
        bias = "LONG_LIQUIDATIONS"
    elif short_liquidations > long_liquidations:
        bias = "SHORT_LIQUIDATIONS"
    else:
        bias = "NEUTRAL"

    return {
        "liquidation_count": count,
        "liquidation_notional": round(notional, 2),
        "liquidation_pressure": bias,
        "sample_window_seconds": listen_seconds,
    }
