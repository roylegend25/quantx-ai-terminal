"""Crypto Fear & Greed Index (alternative.me - free, no key).

`history(limit)` returns normalized daily rows for sentiment_history;
`current()` is the cached latest reading."""

import httpx

from app.data_sources.cache import cache

FEAR_GREED_URL = "https://api.alternative.me/fng/"


async def _fetch_raw(limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(FEAR_GREED_URL, params={"limit": limit})
        r.raise_for_status()
        return r.json().get("data", [])


async def current() -> dict:
    async def _fetch():
        rows = await _fetch_raw(1)
        row = rows[0]
        return {
            "value": int(row["value"]),
            "label": row["value_classification"],
            "time": int(row["timestamp"]) * 1000,
            "provider": "alternative.me",
        }

    value, is_stale = await cache.get_or_fetch("fear_greed:current", _fetch, ttl_seconds=1800)
    return {**value, "stale": is_stale}


async def history(limit: int = 365) -> list[dict]:
    """Normalized rows: {"time" (ms, day resolution), "metric", "value", "label"}."""
    rows = await _fetch_raw(limit)
    return [
        {
            "time": int(r["timestamp"]) * 1000,
            "metric": "fear_greed",
            "value": float(r["value"]),
            "label": r.get("value_classification"),
        }
        for r in rows
    ]
