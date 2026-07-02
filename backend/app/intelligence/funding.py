import httpx

BINANCE_FAPI = "https://fapi.binance.com"


async def fetch(symbol: str) -> dict:
    """Current funding rate for a perpetual future.

    Positive funding = longs pay shorts (crowd leaning long).
    Negative funding = shorts pay longs (crowd leaning short).
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/premiumIndex",
            params={"symbol": symbol.upper()},
        )
        r.raise_for_status()
        data = r.json()

    return {
        "funding_rate": float(data["lastFundingRate"]),
        "mark_price": float(data["markPrice"]),
        "index_price": float(data["indexPrice"]),
        "next_funding_time": data.get("nextFundingTime"),
    }
