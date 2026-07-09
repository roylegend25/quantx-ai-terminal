"""Optional Glassnode provider (on-chain indicators such as SOPR/NUPL).

Requires GLASSNODE_API_KEY in the environment. Without it every call reports
the provider as unavailable instead of crashing - callers degrade to the
free Binance-derived data. No key is ever hardcoded or logged.
"""

import os

import httpx

GLASSNODE_BASE = os.getenv("GLASSNODE_API_URL", "https://api.glassnode.com")


class ProviderUnavailable(Exception):
    pass


def api_key() -> str | None:
    return os.getenv("GLASSNODE_API_KEY") or None


def available() -> bool:
    return api_key() is not None


def status() -> dict:
    return {
        "provider": "glassnode",
        "available": available(),
        "reason": None if available() else "GLASSNODE_API_KEY not configured",
    }


def _asset(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT")


async def _get(path: str, params: dict | None = None) -> list:
    key = api_key()
    if not key:
        raise ProviderUnavailable("GLASSNODE_API_KEY not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{GLASSNODE_BASE}{path}",
            params={"api_key": key, **{k: v for k, v in (params or {}).items() if v is not None}},
        )
        r.raise_for_status()
        return r.json()


async def fetch_metric(symbol: str, metric: str = "indicators/sopr", interval: str = "1h", limit: int = 500) -> list[dict]:
    """Any Glassnode /v1/metrics series, normalized to {time, value}.
    Glassnode timestamps are unix seconds; stored as epoch ms like the rest
    of the data engine."""
    rows = await _get(f"/v1/metrics/{metric}", {"a": _asset(symbol), "i": interval})
    rows = rows[-limit:] if limit else rows
    return [
        {"time": int(r.get("t") or 0) * 1000, "value": float(r["v"])}
        for r in rows
        if r.get("v") is not None
    ]
