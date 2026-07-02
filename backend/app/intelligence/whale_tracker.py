import httpx

BINANCE_FAPI = "https://fapi.binance.com"

DEFAULT_NOTIONAL_THRESHOLD = 100_000.0


async def fetch(symbol: str, notional_threshold: float = DEFAULT_NOTIONAL_THRESHOLD, limit: int = 1000) -> dict:
    """Scans recent aggregated trades for large ("whale") prints.

    A trade's aggressor is the buyer when isBuyerMaker (`m`) is False,
    and the seller when `m` is True.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/aggTrades",
            params={"symbol": symbol.upper(), "limit": limit},
        )
        r.raise_for_status()
        trades = r.json()

    count = 0
    net_flow = 0.0
    for t in trades:
        notional = float(t["p"]) * float(t["q"])
        if notional < notional_threshold:
            continue
        count += 1
        is_buyer_maker = t["m"]
        net_flow += -notional if is_buyer_maker else notional

    if count == 0:
        bias = "NEUTRAL"
    elif net_flow > 0:
        bias = "BUY"
    else:
        bias = "SELL"

    return {
        "whale_trade_count": count,
        "whale_net_flow": round(net_flow, 2),
        "whale_bias": bias,
    }
