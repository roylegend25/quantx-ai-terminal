import httpx
import pandas as pd
from pathlib import Path

BINANCE_FAPI = "https://fapi.binance.com"

async def download_klines(symbol: str, interval: str = "5m", limit: int = 1000):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        r.raise_for_status()

    rows = []
    for k in r.json():
        rows.append({
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    return pd.DataFrame(rows)

async def save_history(symbol: str, interval: str = "5m", limit: int = 1000):
    df = await download_klines(symbol, interval, limit)

    path = Path(f"/app/data/history/{symbol.upper()}")
    path.mkdir(parents=True, exist_ok=True)

    file = path / f"{interval}.csv"
    df.to_csv(file, index=False)

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "rows": len(df),
        "file": str(file),
    }
