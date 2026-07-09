"""Optional CryptoQuant provider (exchange flows / on-chain flow metrics).

Requires CRYPTOQUANT_API_KEY in the environment. Without it every call
reports the provider as unavailable instead of crashing - callers degrade to
the free Binance-derived data. No key is ever hardcoded or logged.
"""

import os

import httpx

CRYPTOQUANT_BASE = os.getenv("CRYPTOQUANT_API_URL", "https://api.cryptoquant.com/v1")


class ProviderUnavailable(Exception):
    pass


def api_key() -> str | None:
    return os.getenv("CRYPTOQUANT_API_KEY") or None


def available() -> bool:
    return api_key() is not None


def status() -> dict:
    return {
        "provider": "cryptoquant",
        "available": available(),
        "reason": None if available() else "CRYPTOQUANT_API_KEY not configured",
    }


def _asset(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT").lower()


async def _get(path: str, params: dict | None = None) -> dict:
    key = api_key()
    if not key:
        raise ProviderUnavailable("CRYPTOQUANT_API_KEY not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{CRYPTOQUANT_BASE}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            headers={"Authorization": f"Bearer {key}", "accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def fetch_exchange_netflow(symbol: str, window: str = "hour", limit: int = 500) -> list[dict]:
    """Net BTC/ETH flow into exchanges (positive = inflow, bearish-leaning)."""
    payload = await _get(
        f"/{_asset(symbol)}/exchange-flows/netflow",
        {"exchange": "binance", "window": window, "limit": limit},
    )
    rows = (payload.get("result") or {}).get("data") or []
    return [
        {
            "time": int(r.get("timestamp") or 0),
            "netflow": float(r.get("netflow_total") or r.get("value") or 0.0),
        }
        for r in rows
    ]
