"""Optional Hyblock Capital provider (liquidation levels, order-flow bias).

Requires HYBLOCK_API_KEY in the environment. Without it every call reports
the provider as unavailable instead of crashing - callers degrade to the
free Binance-derived data. No key is ever hardcoded or logged.
"""

import os

import httpx

HYBLOCK_BASE = os.getenv("HYBLOCK_API_URL", "https://api1.hyblockcapital.com/v1")


class ProviderUnavailable(Exception):
    pass


def api_key() -> str | None:
    return os.getenv("HYBLOCK_API_KEY") or None


def available() -> bool:
    return api_key() is not None


def status() -> dict:
    return {
        "provider": "hyblock",
        "available": available(),
        "reason": None if available() else "HYBLOCK_API_KEY not configured",
    }


async def _get(path: str, params: dict | None = None) -> dict:
    key = api_key()
    if not key:
        raise ProviderUnavailable("HYBLOCK_API_KEY not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{HYBLOCK_BASE}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            headers={"x-api-key": key, "accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def fetch_liquidation_levels(symbol: str, timeframe: str = "1h", limit: int = 200) -> list[dict]:
    """Estimated liquidation-level clusters around price."""
    payload = await _get(
        "/liquidationLevels",
        {"coin": symbol.upper().removesuffix("USDT"), "timeframe": timeframe, "limit": limit, "exchange": "binance"},
    )
    rows = payload.get("data") or []
    return [
        {
            "time": int(r.get("openTime") or r.get("t") or 0) * (1000 if int(r.get("openTime") or r.get("t") or 0) < 10**12 else 1),
            "price": float(r.get("price") or 0.0),
            "liquidation_usd": float(r.get("liqValue") or r.get("value") or 0.0),
            "side": "LONG" if (r.get("side") or "").lower() in ("long", "buy") else "SHORT",
        }
        for r in rows
    ]
