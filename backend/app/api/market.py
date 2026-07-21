import time

from fastapi import APIRouter, HTTPException
import httpx

from app.core.config import settings
from app.core.response_meta import market_meta
from app.intelligence import hyperliquid_trades, liquidation_heatmap, market_intelligence

router = APIRouter(prefix="/api/market", tags=["market"])

BINANCE_FAPI = "https://fapi.binance.com"

# registered before /{symbol} so "context"/"hyperliquid" aren't swallowed as a symbol
@router.get("/context")
async def get_market_context(symbol: str = settings.default_symbol):
    return await market_intelligence.get_context(symbol)

@router.get("/hyperliquid/large-trades")
async def get_hyperliquid_large_trades(coins: str = "BTC,ETH", min_notional: float = hyperliquid_trades.DEFAULT_MIN_NOTIONAL):
    coin_list = tuple(c.strip().upper() for c in coins.split(",") if c.strip())
    coin_list = tuple(c for c in coin_list if c in hyperliquid_trades.SUPPORTED_COINS) or hyperliquid_trades.SUPPORTED_COINS
    min_notional = max(0.0, min_notional)
    return await hyperliquid_trades.fetch(coins=coin_list, min_notional=min_notional)

@router.get("/{symbol}")
async def get_market(symbol: str):
    symbol = symbol.upper()
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            ticker = await client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol})
            funding = await client.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
            oi = await client.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})

            ticker.raise_for_status()
            funding.raise_for_status()
            oi.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Binance market data: {exc}")

    latency_ms = (time.monotonic() - started) * 1000
    ticker_json = ticker.json()
    close_time_ms = ticker_json.get("closeTime")

    return {
        "symbol": symbol,
        "ticker": ticker_json,
        "funding": funding.json(),
        "open_interest": oi.json(),
        **market_meta(
            source="binance_futures", source_type="exchange_rest",
            market_timestamp=(close_time_ms / 1000) if close_time_ms else None,
            latency_ms=latency_ms,
        ),
    }

@router.get("/{symbol}/liquidation-heatmap")
async def get_liquidation_heatmap(symbol: str):
    symbol = symbol.upper()
    try:
        return await liquidation_heatmap.build(symbol)
    except Exception as exc:
        return liquidation_heatmap.degraded(symbol, error=str(exc))

@router.get("/{symbol}/candles")
async def get_candles(symbol: str, interval: str = "5m", limit: int = 220):
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        r.raise_for_status()

    return [
        {
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in r.json()
    ]
