"""Binance spot market-data fetchers - the fallback/reference venue when a
symbol isn't listed on futures or a cross-check price is wanted. Public
endpoints only; same raw payload shapes as binance_futures.py."""

import os

import httpx

BINANCE_SPOT = os.getenv("BINANCE_SPOT_URL", "https://api.binance.com")
MAX_KLINES_PER_REQUEST = 1000
MAX_PAGES = 40

_TIMEOUT = httpx.Timeout(20.0)


async def _get(path: str, params: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{BINANCE_SPOT}{path}", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r


async def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
) -> list[list]:
    symbol = symbol.upper()
    out: list[list] = []
    remaining = max(1, int(limit))

    if start_ms is None:
        while remaining > 0 and len(out) // MAX_KLINES_PER_REQUEST < MAX_PAGES:
            batch_limit = min(remaining, MAX_KLINES_PER_REQUEST)
            r = await _get(
                "/api/v3/klines",
                {"symbol": symbol, "interval": interval, "limit": batch_limit, "endTime": end_ms},
            )
            batch = r.json()
            if not batch:
                break
            out = batch + out
            remaining -= len(batch)
            if len(batch) < batch_limit:
                break
            end_ms = int(batch[0][0]) - 1
        return out

    cursor = int(start_ms)
    for _ in range(MAX_PAGES):
        if remaining <= 0:
            break
        batch_limit = min(remaining, MAX_KLINES_PER_REQUEST)
        r = await _get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": batch_limit},
        )
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        remaining -= len(batch)
        cursor = int(batch[-1][0]) + 1
        if len(batch) < batch_limit or (end_ms is not None and cursor > end_ms):
            break
    return out


async def fetch_ticker(symbol: str) -> dict:
    r = await _get("/api/v3/ticker/24hr", {"symbol": symbol.upper()})
    return r.json()
