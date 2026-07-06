"""Binance USDⓈ-M futures market-data fetchers (public endpoints, no key).

Everything here is a thin async wrapper that returns raw provider payloads;
normalization lives in app/data_sources/normalizer.py. Klines paginate with
startTime so historical ranges far beyond one request's limit can be pulled.
"""

import os

import httpx

BINANCE_FAPI = os.getenv("BINANCE_FAPI_URL", "https://fapi.binance.com")
MAX_KLINES_PER_REQUEST = 1500
MAX_PAGES = 40  # hard ceiling: 60k candles per download call

_TIMEOUT = httpx.Timeout(20.0)


async def _get(path: str, params: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{BINANCE_FAPI}{path}", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r


async def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
) -> list[list]:
    """Paginated kline fetch. With start_ms, pages forward until end_ms/now or
    `limit` rows; without it, returns the most recent `limit` rows."""
    symbol = symbol.upper()
    out: list[list] = []
    remaining = max(1, int(limit))

    if start_ms is None:
        while remaining > 0 and len(out) // MAX_KLINES_PER_REQUEST < MAX_PAGES:
            batch_limit = min(remaining, MAX_KLINES_PER_REQUEST)
            r = await _get(
                "/fapi/v1/klines",
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
            "/fapi/v1/klines",
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
    r = await _get("/fapi/v1/ticker/24hr", {"symbol": symbol.upper()})
    return r.json()


async def fetch_premium_index(symbol: str) -> dict:
    """Current mark/index price + last funding rate."""
    r = await _get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
    return r.json()


async def fetch_funding_history(
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    r = await _get(
        "/fapi/v1/fundingRate",
        {"symbol": symbol.upper(), "startTime": start_ms, "endTime": end_ms, "limit": min(limit, 1000)},
    )
    return r.json()


async def fetch_open_interest(symbol: str) -> dict:
    r = await _get("/fapi/v1/openInterest", {"symbol": symbol.upper()})
    return r.json()


async def fetch_open_interest_hist(
    symbol: str,
    period: str = "5m",
    limit: int = 500,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[dict]:
    r = await _get(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol.upper(),
            "period": period,
            "limit": min(limit, 500),
            "startTime": start_ms,
            "endTime": end_ms,
        },
    )
    return r.json()


async def fetch_orderbook(symbol: str, limit: int = 100) -> dict:
    r = await _get("/fapi/v1/depth", {"symbol": symbol.upper(), "limit": limit})
    return r.json()


async def fetch_agg_trades(symbol: str, limit: int = 1000) -> list[dict]:
    r = await _get("/fapi/v1/aggTrades", {"symbol": symbol.upper(), "limit": min(limit, 1000)})
    return r.json()
