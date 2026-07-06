"""Optional Coinglass provider (liquidations, open interest, funding).

Requires COINGLASS_API_KEY in the environment. Without it every call reports
the provider as unavailable instead of crashing - callers degrade to the
free Binance-derived data. No key is ever hardcoded or logged.
"""

import os

import httpx

COINGLASS_BASE = os.getenv("COINGLASS_API_URL", "https://open-api-v3.coinglass.com")


class ProviderUnavailable(Exception):
    pass


def api_key() -> str | None:
    return os.getenv("COINGLASS_API_KEY") or None


def available() -> bool:
    return api_key() is not None


def status() -> dict:
    return {
        "provider": "coinglass",
        "available": available(),
        "reason": None if available() else "COINGLASS_API_KEY not configured",
    }


async def _get(path: str, params: dict | None = None) -> dict:
    key = api_key()
    if not key:
        raise ProviderUnavailable("COINGLASS_API_KEY not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{COINGLASS_BASE}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            headers={"CG-API-KEY": key, "accept": "application/json"},
        )
        r.raise_for_status()
        payload = r.json()
    if str(payload.get("code", "0")) not in ("0", "200") and payload.get("success") is not True:
        raise ProviderUnavailable(f"Coinglass error: {payload.get('msg', 'unknown')}")
    return payload


async def fetch_liquidation_history(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    """Aggregated long/short liquidation notionals per bar."""
    payload = await _get(
        "/api/futures/liquidation/v2/history",
        {"exchange": "Binance", "symbol": symbol.upper(), "interval": interval, "limit": limit},
    )
    rows = payload.get("data") or []
    return [
        {
            "time": int(r.get("t") or r.get("createTime") or 0),
            "long_liquidation_usd": float(r.get("longLiquidationUsd") or r.get("buyVolUsd") or 0.0),
            "short_liquidation_usd": float(r.get("shortLiquidationUsd") or r.get("sellVolUsd") or 0.0),
        }
        for r in rows
    ]


async def fetch_open_interest_history(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    payload = await _get(
        "/api/futures/openInterest/ohlc-history",
        {"exchange": "Binance", "symbol": symbol.upper(), "interval": interval, "limit": limit},
    )
    rows = payload.get("data") or []
    return [
        {"time": int(r.get("t") or 0), "open_interest_value": float(r.get("c") or 0.0)}
        for r in rows
    ]


async def fetch_funding_history(symbol: str, interval: str = "8h", limit: int = 500) -> list[dict]:
    payload = await _get(
        "/api/futures/fundingRate/ohlc-history",
        {"exchange": "Binance", "symbol": symbol.upper(), "interval": interval, "limit": limit},
    )
    rows = payload.get("data") or []
    return [{"time": int(r.get("t") or 0), "funding_rate": float(r.get("c") or 0.0)} for r in rows]
