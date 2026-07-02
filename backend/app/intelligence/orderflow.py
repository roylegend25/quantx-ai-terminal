import asyncio

import httpx

BINANCE_FAPI = "https://fapi.binance.com"


async def _fetch_long_short_ratio(client: httpx.AsyncClient, symbol: str) -> float | None:
    r = await client.get(
        f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol, "period": "5m", "limit": 1},
    )
    r.raise_for_status()
    rows = r.json()
    return float(rows[-1]["longShortRatio"]) if rows else None


async def _fetch_buy_sell_ratio(client: httpx.AsyncClient, symbol: str) -> dict | None:
    r = await client.get(
        f"{BINANCE_FAPI}/futures/data/takerlongshortRatio",
        params={"symbol": symbol, "period": "5m", "limit": 1},
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    row = rows[-1]
    return {
        "buy_sell_ratio": float(row["buySellRatio"]),
        "buy_volume": float(row["buyVol"]),
        "sell_volume": float(row["sellVol"]),
    }


async def _fetch_bid_ask_ratio(client: httpx.AsyncClient, symbol: str, depth: int = 20) -> float | None:
    r = await client.get(
        f"{BINANCE_FAPI}/fapi/v1/depth",
        params={"symbol": symbol, "limit": depth},
    )
    r.raise_for_status()
    data = r.json()
    bid_qty = sum(float(q) for _, q in data["bids"])
    ask_qty = sum(float(q) for _, q in data["asks"])
    total = bid_qty + ask_qty
    return round(bid_qty / total, 4) if total > 0 else None


async def _fetch_cvd(client: httpx.AsyncClient, symbol: str, interval: str = "5m", limit: int = 50) -> float | None:
    """Cumulative Volume Delta over the recent window, from kline taker-buy volume."""
    r = await client.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    r.raise_for_status()
    klines = r.json()
    if not klines:
        return None

    cvd = 0.0
    for k in klines:
        total_volume = float(k[5])
        taker_buy_volume = float(k[9])
        taker_sell_volume = total_volume - taker_buy_volume
        cvd += taker_buy_volume - taker_sell_volume

    return round(cvd, 4)


async def fetch(symbol: str) -> dict:
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=10) as client:
        long_short_ratio, taker_volume, bid_ask_ratio, cvd = await asyncio.gather(
            _fetch_long_short_ratio(client, symbol),
            _fetch_buy_sell_ratio(client, symbol),
            _fetch_bid_ask_ratio(client, symbol),
            _fetch_cvd(client, symbol),
        )

    return {
        "long_short_ratio": long_short_ratio,
        "buy_sell_ratio": taker_volume["buy_sell_ratio"] if taker_volume else None,
        "buy_volume": taker_volume["buy_volume"] if taker_volume else None,
        "sell_volume": taker_volume["sell_volume"] if taker_volume else None,
        "bid_ask_ratio": bid_ask_ratio,
        "cvd": cvd,
    }
