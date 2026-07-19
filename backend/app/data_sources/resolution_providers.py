"""Multi-exchange HistoricalResolutionPriceProvider used only by the
prediction resolver's catch-up path (app/decision_engine/resolver.py) to
backfill a due prediction's outcome candle when the primary source
(Binance USDT-M futures - the source every prediction was generated against)
has a verified data gap.

Every fetcher here is read-only, public-endpoint, no credentials. This module
never writes a trade and is never imported by the live decision/execution
path - it only feeds historical OHLC into app.data_sources.downloader.store_candles
for resolution purposes.

Provider priority (config RESOLVER_PROVIDER_PRIORITY, see app/core/config.py):
  1. binance_futures (the original source for every prediction today)
  2. bybit
  3. okx
  4. hyperliquid
  5. binance_spot (only if RESOLVER_ALLOW_SPOT_FALLBACK=true, clearly labelled)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.data_sources import symbol_map
from app.monitoring.logging import get_logger

logger = get_logger("quantx.resolver.providers")

_TIMEOUT = httpx.Timeout(float(__import__("os").environ.get("RESOLVER_PROVIDER_TIMEOUT", "10")))

# Interval-string translation from this app's canonical timeframe (see
# app/data_sources/normalizer.py TIMEFRAMES_MS) to each exchange's own enum.
# "1M" (calendar month) is deliberately absent everywhere - see resolver.py.
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120",
                    "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W"}
_OKX_INTERVAL = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H",
                  "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W"}
_HYPERLIQUID_INTERVAL = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h",
                          "2h": "2h", "4h": "4h", "12h": "12h", "1d": "1d", "1w": "1w"}  # no 6h on Hyperliquid


@dataclass
class ResolutionPriceObservation:
    provider: str
    exchange: str
    market_type: str
    symbol: str
    requested_timestamp: int
    actual_market_timestamp: int | None = None
    price: float | None = None
    price_type: str = "close"
    latency_ms: float | None = None
    stale: bool = False
    confidence: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.price is not None


async def _timed_get(url: str, params: dict | None = None, method: str = "GET", json_body: dict | None = None) -> tuple[dict | list | None, float, str | None]:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if method == "POST":
                r = await client.post(url, json=json_body)
            else:
                r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json(), (time.monotonic() - start) * 1000, None
    except (httpx.HTTPError, ValueError) as exc:
        return None, (time.monotonic() - start) * 1000, repr(exc)


async def fetch_binance_futures(canonical_symbol: str, timeframe: str, at_ms: int, timeframe_ms: int) -> ResolutionPriceObservation:
    from app.data_sources.normalizer import TIMEFRAMES_MS

    es = symbol_map.provider_symbol(canonical_symbol, "binance_futures")
    obs = ResolutionPriceObservation("binance_futures", "binance", "usdt_perp", canonical_symbol, at_ms)
    if not es:
        obs.error = "unsupported_symbol"
        return obs
    if timeframe not in TIMEFRAMES_MS:
        obs.error = "unsupported_timeframe"
        return obs
    import os
    fapi = os.getenv("BINANCE_FAPI_URL", "https://fapi.binance.com")
    data, latency, err = await _timed_get(
        f"{fapi}/fapi/v1/klines",
        {"symbol": es.provider_symbol, "interval": timeframe, "startTime": at_ms, "limit": 1},
    )
    obs.latency_ms = latency
    if err or not data:
        obs.error = err or "no_data"
        return obs
    k = data[0]
    obs.actual_market_timestamp = int(k[0])
    obs.price = float(k[4])
    obs.confidence = 1.0
    obs.stale = obs.actual_market_timestamp > at_ms + timeframe_ms * 2
    return obs


async def fetch_binance_spot(canonical_symbol: str, timeframe: str, at_ms: int, timeframe_ms: int) -> ResolutionPriceObservation:
    from app.data_sources.normalizer import TIMEFRAMES_MS

    es = symbol_map.provider_symbol(canonical_symbol, "binance_spot")
    obs = ResolutionPriceObservation("binance_spot", "binance", "spot", canonical_symbol, at_ms)
    if not es or timeframe not in TIMEFRAMES_MS:
        obs.error = "unsupported_timeframe"
        return obs
    data, latency, err = await _timed_get(
        "https://api.binance.com/api/v3/klines",
        {"symbol": es.provider_symbol, "interval": timeframe, "startTime": at_ms, "limit": 1},
    )
    obs.latency_ms = latency
    if err or not data:
        obs.error = err or "no_data"
        return obs
    k = data[0]
    obs.actual_market_timestamp = int(k[0])
    obs.price = float(k[4])
    obs.confidence = 0.6  # spot != perp basis - always a lower-confidence fallback
    obs.stale = obs.actual_market_timestamp > at_ms + timeframe_ms * 2
    return obs


async def fetch_bybit(canonical_symbol: str, timeframe: str, at_ms: int, timeframe_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "bybit")
    obs = ResolutionPriceObservation("bybit", "bybit", "usdt_perp", canonical_symbol, at_ms)
    interval = _BYBIT_INTERVAL.get(timeframe)
    if not es or not interval:
        obs.error = "unsupported_timeframe"
        return obs
    data, latency, err = await _timed_get(
        "https://api.bybit.com/v5/market/kline",
        {"category": "linear", "symbol": es.provider_symbol, "interval": interval, "start": at_ms, "limit": 1},
    )
    obs.latency_ms = latency
    if err:
        obs.error = err
        return obs
    rows = ((data or {}).get("result") or {}).get("list") or []
    if not rows:
        obs.error = "no_data"
        return obs
    row = rows[0]  # bybit returns most-recent-first; a start= filter still yields the closest bar at/after start
    obs.actual_market_timestamp = int(row[0])
    obs.price = float(row[4])
    obs.confidence = 0.9
    obs.stale = obs.actual_market_timestamp > at_ms + timeframe_ms * 2
    return obs


async def fetch_okx(canonical_symbol: str, timeframe: str, at_ms: int, timeframe_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "okx")
    obs = ResolutionPriceObservation("okx", "okx", "usdt_swap", canonical_symbol, at_ms)
    bar = _OKX_INTERVAL.get(timeframe)
    if not es or not bar:
        obs.error = "unsupported_timeframe"
        return obs
    # OKX history-candles pages backward from `before` (exclusive); ask for a
    # small window starting just before the target and pick the first bar >= at_ms.
    data, latency, err = await _timed_get(
        "https://www.okx.com/api/v5/market/history-candles",
        {"instId": es.provider_symbol, "bar": bar, "after": str(at_ms + timeframe_ms * 3), "limit": "5"},
    )
    obs.latency_ms = latency
    if err:
        obs.error = err
        return obs
    rows = (data or {}).get("data") or []
    candidates = [r for r in rows if int(r[0]) >= at_ms]
    if not candidates:
        obs.error = "no_data"
        return obs
    row = min(candidates, key=lambda r: int(r[0]))
    obs.actual_market_timestamp = int(row[0])
    obs.price = float(row[4])
    obs.confidence = 0.9
    obs.stale = obs.actual_market_timestamp > at_ms + timeframe_ms * 2
    return obs


async def fetch_hyperliquid(canonical_symbol: str, timeframe: str, at_ms: int, timeframe_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "hyperliquid")
    obs = ResolutionPriceObservation("hyperliquid", "hyperliquid", "usdt_perp", canonical_symbol, at_ms)
    interval = _HYPERLIQUID_INTERVAL.get(timeframe)
    if not es or not interval:
        obs.error = "unsupported_timeframe"
        return obs
    data, latency, err = await _timed_get(
        "https://api.hyperliquid.xyz/info",
        method="POST",
        json_body={"type": "candleSnapshot", "req": {"coin": es.provider_symbol, "interval": interval,
                                                       "startTime": at_ms, "endTime": at_ms + timeframe_ms * 3}},
    )
    obs.latency_ms = latency
    if err:
        obs.error = err
        return obs
    rows = data or []
    candidates = [r for r in rows if int(r.get("t", -1)) >= at_ms]
    if not candidates:
        obs.error = "no_data"
        return obs
    row = min(candidates, key=lambda r: int(r["t"]))
    obs.actual_market_timestamp = int(row["t"])
    obs.price = float(row["c"])
    obs.confidence = 0.8
    obs.stale = obs.actual_market_timestamp > at_ms + timeframe_ms * 2
    return obs


PROVIDER_FETCHERS = {
    "binance_futures": fetch_binance_futures,
    "bybit": fetch_bybit,
    "okx": fetch_okx,
    "hyperliquid": fetch_hyperliquid,
    "binance_spot": fetch_binance_spot,
}
