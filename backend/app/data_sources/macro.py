"""Market-wide macro context: BTC dominance and stablecoin dominance from
CoinGecko's free /global endpoint. Point-in-time only (the free endpoint has
no history), so repeated scheduler runs accumulate a real history in
sentiment_history. Degrades gracefully when unreachable."""

import time

import httpx

from app.data_sources.cache import cache

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

STABLECOINS = ("usdt", "usdc", "dai")


async def current() -> dict:
    async def _fetch():
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(COINGECKO_GLOBAL)
            r.raise_for_status()
            data = r.json().get("data", {})

        pct = data.get("market_cap_percentage", {}) or {}
        btc_dominance = float(pct.get("btc")) if pct.get("btc") is not None else None
        stable = sum(float(pct[c]) for c in STABLECOINS if pct.get(c) is not None)

        return {
            "time": int((data.get("updated_at") or time.time()) * 1000),
            "btc_dominance": round(btc_dominance, 4) if btc_dominance is not None else None,
            "stablecoin_dominance": round(stable, 4) if stable else None,
            "provider": "coingecko",
        }

    value, is_stale = await cache.get_or_fetch("macro:global", _fetch, ttl_seconds=900)
    return {**value, "stale": is_stale}


async def rows() -> list[dict]:
    """Normalized sentiment_history rows for whatever metrics are available."""
    snap = await current()
    out = []
    for metric in ("btc_dominance", "stablecoin_dominance"):
        if snap.get(metric) is not None:
            out.append({"time": snap["time"], "metric": metric, "value": snap[metric], "label": None})
    return out
