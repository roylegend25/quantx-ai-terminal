import httpx

BINANCE_FAPI = "https://fapi.binance.com"


async def fetch(symbol: str, period: str = "5m") -> dict:
    """Current open interest and its % change over the last period.

    Rising OI = new positions being opened (trend building).
    Falling OI = positions being closed (trend unwinding).
    """
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=10) as client:
        current = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/openInterest",
            params={"symbol": symbol},
        )
        current.raise_for_status()

        hist = await client.get(
            f"{BINANCE_FAPI}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": 2},
        )
        hist.raise_for_status()

    open_interest = float(current.json()["openInterest"])

    rows = hist.json()
    oi_change_pct = 0.0
    if len(rows) >= 2:
        previous = float(rows[-2]["sumOpenInterest"])
        latest = float(rows[-1]["sumOpenInterest"])
        if previous > 0:
            oi_change_pct = round((latest - previous) / previous * 100, 4)

    return {
        "open_interest": open_interest,
        "oi_change_pct": oi_change_pct,
    }
