"""Multi-exchange HistoricalResolutionPriceProvider used only by the
prediction resolver's catch-up path (app/decision_engine/resolver.py
_fetch_and_store_backfill) to backfill a due prediction's outcome candle
when the primary source (Binance USDT-M futures - the exchange every
prediction was generated against) has a verified data gap.

Every fetcher here is read-only, public-endpoint, no credentials. This
module never writes a trade and is never imported by the live decision/
execution path - it only feeds historical OHLC into
app.data_sources.downloader.store_candles for resolution purposes.

Provider priority (config RESOLVER_PROVIDER_PRIORITY-equivalent, hardcoded
in resolver.py's _FALLBACK_ORDER):
  1. binance_futures (the original source for every prediction today)
  2. bybit
  3. okx
  4. hyperliquid
  5. binance_spot (only if settings.resolver_allow_spot_fallback, clearly labelled)

"1M" (calendar month) is deliberately unsupported everywhere in this module -
same reasoning as app/timeframes/canonical.py's timeframe_ms: a calendar
month has no fixed duration, and none of these fallback providers' interval
enums are worth mapping for a bucket this codebase already treats specially.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from app.data_sources import symbol_map
from app.monitoring.logging import get_logger

logger = get_logger("quantx.resolver.providers")

_TIMEOUT = httpx.Timeout(float(os.environ.get("RESOLVER_PROVIDER_TIMEOUT", "10")))

# A small forward window from the target timestamp - mirrors resolver.py's
# own _fetch_and_store_backfill window (6h) for the primary provider.
_WINDOW_MS = 6 * 3_600_000

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
    confidence: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.price is not None


async def _timed(method: str, url: str, params: dict | None = None, json_body: dict | None = None):
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await (client.post(url, json=json_body) if method == "POST" else client.get(url, params=params))
            r.raise_for_status()
            return r.json(), (time.monotonic() - start) * 1000, None
    except (httpx.HTTPError, ValueError) as exc:
        return None, (time.monotonic() - start) * 1000, repr(exc)


async def fetch_binance_futures(canonical_symbol: str, timeframe: str, at_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "binance_futures")
    obs = ResolutionPriceObservation("binance_futures", "binance", "usdt_perp", canonical_symbol, at_ms)
    if not es or timeframe == "1M":
        obs.error = "unsupported_symbol" if not es else "unsupported_timeframe"
        return obs
    fapi = os.getenv("BINANCE_FAPI_URL", "https://fapi.binance.com")
    data, latency, err = await _timed("GET", f"{fapi}/fapi/v1/klines",
                                       {"symbol": es.provider_symbol, "interval": timeframe, "startTime": at_ms, "limit": 1})
    obs.latency_ms = latency
    if err or not data:
        obs.error = err or "no_data"
        return obs
    k = data[0]
    obs.actual_market_timestamp, obs.price, obs.confidence = int(k[0]), float(k[4]), 1.0
    return obs


async def fetch_binance_spot(canonical_symbol: str, timeframe: str, at_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "binance_spot")
    obs = ResolutionPriceObservation("binance_spot", "binance", "spot", canonical_symbol, at_ms)
    if not es or timeframe == "1M":
        obs.error = "unsupported_symbol" if not es else "unsupported_timeframe"
        return obs
    data, latency, err = await _timed("GET", "https://api.binance.com/api/v3/klines",
                                       {"symbol": es.provider_symbol, "interval": timeframe, "startTime": at_ms, "limit": 1})
    obs.latency_ms = latency
    if err or not data:
        obs.error = err or "no_data"
        return obs
    k = data[0]
    obs.actual_market_timestamp, obs.price, obs.confidence = int(k[0]), float(k[4]), 0.6  # spot != perp basis
    return obs


async def fetch_bybit(canonical_symbol: str, timeframe: str, at_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "bybit")
    obs = ResolutionPriceObservation("bybit", "bybit", "usdt_perp", canonical_symbol, at_ms)
    interval = _BYBIT_INTERVAL.get(timeframe)
    if not es or not interval:
        obs.error = "unsupported_symbol" if not es else "unsupported_timeframe"
        return obs
    data, latency, err = await _timed("GET", "https://api.bybit.com/v5/market/kline",
                                       {"category": "linear", "symbol": es.provider_symbol, "interval": interval, "start": at_ms, "limit": 1})
    obs.latency_ms = latency
    if err:
        obs.error = err
        return obs
    rows = ((data or {}).get("result") or {}).get("list") or []
    if not rows:
        obs.error = "no_data"
        return obs
    row = rows[0]
    obs.actual_market_timestamp, obs.price, obs.confidence = int(row[0]), float(row[4]), 0.9
    return obs


async def fetch_okx(canonical_symbol: str, timeframe: str, at_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "okx")
    obs = ResolutionPriceObservation("okx", "okx", "usdt_swap", canonical_symbol, at_ms)
    bar = _OKX_INTERVAL.get(timeframe)
    if not es or not bar:
        obs.error = "unsupported_symbol" if not es else "unsupported_timeframe"
        return obs
    data, latency, err = await _timed("GET", "https://www.okx.com/api/v5/market/history-candles",
                                       {"instId": es.provider_symbol, "bar": bar, "after": str(at_ms + _WINDOW_MS), "limit": "5"})
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
    obs.actual_market_timestamp, obs.price, obs.confidence = int(row[0]), float(row[4]), 0.9
    return obs


async def fetch_hyperliquid(canonical_symbol: str, timeframe: str, at_ms: int) -> ResolutionPriceObservation:
    es = symbol_map.provider_symbol(canonical_symbol, "hyperliquid")
    obs = ResolutionPriceObservation("hyperliquid", "hyperliquid", "usdt_perp", canonical_symbol, at_ms)
    interval = _HYPERLIQUID_INTERVAL.get(timeframe)
    if not es or not interval:
        obs.error = "unsupported_symbol" if not es else "unsupported_timeframe"
        return obs
    data, latency, err = await _timed("POST", "https://api.hyperliquid.xyz/info", json_body={
        "type": "candleSnapshot", "req": {"coin": es.provider_symbol, "interval": interval,
                                           "startTime": at_ms, "endTime": at_ms + _WINDOW_MS}})
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
    obs.actual_market_timestamp, obs.price, obs.confidence = int(row["t"]), float(row["c"]), 0.8
    return obs


PROVIDER_FETCHERS = {
    "binance_futures": fetch_binance_futures,
    "bybit": fetch_bybit,
    "okx": fetch_okx,
    "hyperliquid": fetch_hyperliquid,
    "binance_spot": fetch_binance_spot,
}
