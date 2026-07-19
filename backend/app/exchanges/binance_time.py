"""Shared, product-scoped Binance server-time synchronization.

Signed Binance timestamps are milliseconds since the Unix epoch in UTC.  This
module deliberately keeps wall-clock and monotonic-clock duties separate:
wall time supplies the epoch, while monotonic time measures network RTT.  A
robust median of low-latency midpoint estimates is shared by every client in
the process, avoiding the former per-client stale/inconsistent offsets.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

import httpx

from app.exchanges.binance_errors import BinanceNetworkError, BinanceTimestampUnsafe
from app.monitoring.logging import get_logger, log_event


class BinanceProduct(str, Enum):
    SPOT = "spot"
    USD_M_FUTURES = "usd_m_futures"
    USD_M_FUTURES_TESTNET = "usd_m_futures_testnet"
    COIN_M_FUTURES = "coin_m_futures"


@dataclass(frozen=True)
class ProductClockEndpoint:
    base_url: str
    time_path: str


ENDPOINTS = {
    BinanceProduct.SPOT: ProductClockEndpoint("https://api.binance.com", "/api/v3/time"),
    BinanceProduct.USD_M_FUTURES: ProductClockEndpoint("https://fapi.binance.com", "/fapi/v1/time"),
    BinanceProduct.USD_M_FUTURES_TESTNET: ProductClockEndpoint(
        "https://testnet.binancefuture.com", "/fapi/v1/time"
    ),
    BinanceProduct.COIN_M_FUTURES: ProductClockEndpoint("https://dapi.binance.com", "/dapi/v1/time"),
}


@dataclass
class TimeSample:
    offset_ms: float
    rtt_ms: float
    server_time_ms: int


@dataclass
class ProductClockState:
    status: str = "unsafe"  # synced | degraded | unsafe
    offset_ms: float = 0.0
    last_sync_monotonic: float | None = None
    last_sync_wall_ms: int | None = None
    last_rtt_ms: float | None = None
    offset_mad_ms: float | None = None
    valid_samples: int = 0
    rejected_samples: int = 0
    last_refresh_reason: str | None = None
    last_error: str | None = "never synchronized"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


SampleFetcher = Callable[[BinanceProduct], Awaitable[dict]]


class BinanceTimeService:
    """One shared clock estimate per Binance product family."""

    def __init__(
        self,
        *,
        sample_count: int = 5,
        minimum_valid_samples: int = 3,
        maximum_rtt_ms: float = 1_500.0,
        refresh_interval_seconds: float = 300.0,
        unsafe_after_seconds: float = 900.0,
        maximum_abs_offset_ms: float = 2_000.0,
        maximum_offset_mad_ms: float = 250.0,
        timeout_seconds: float = 3.0,
        fetcher: SampleFetcher | None = None,
    ):
        self.sample_count = sample_count
        self.minimum_valid_samples = minimum_valid_samples
        self.maximum_rtt_ms = maximum_rtt_ms
        self.refresh_interval_seconds = refresh_interval_seconds
        self.unsafe_after_seconds = unsafe_after_seconds
        self.maximum_abs_offset_ms = maximum_abs_offset_ms
        self.maximum_offset_mad_ms = maximum_offset_mad_ms
        self.timeout_seconds = timeout_seconds
        self._fetcher = fetcher
        self._states = {product: ProductClockState() for product in BinanceProduct}
        self._logger = get_logger("quantx.binance_time")

    def state(self, product: BinanceProduct) -> ProductClockState:
        return self._states[product]

    def _age_seconds(self, state: ProductClockState) -> float | None:
        if state.last_sync_monotonic is None:
            return None
        return max(0.0, time.monotonic() - state.last_sync_monotonic)

    def health(self, product: BinanceProduct) -> dict:
        state = self.state(product)
        age = self._age_seconds(state)
        status = state.status
        if age is None or age > self.unsafe_after_seconds:
            status = "unsafe"
        elif age > self.refresh_interval_seconds and status == "synced":
            status = "degraded"
        return {
            "product": product.value,
            "status": status,
            "synced": status == "synced",
            "offset_ms": round(state.offset_ms, 3),
            "round_trip_ms": round(state.last_rtt_ms, 3) if state.last_rtt_ms is not None else None,
            "offset_mad_ms": round(state.offset_mad_ms, 3) if state.offset_mad_ms is not None else None,
            "sample_age_seconds": round(age, 3) if age is not None else None,
            "valid_samples": state.valid_samples,
            "rejected_samples": state.rejected_samples,
            "refresh_reason": state.last_refresh_reason,
            "last_sync_at_ms": state.last_sync_wall_ms,
            "last_error": state.last_error,
            "timestamp_unit": "milliseconds",
        }

    def all_health(self) -> dict[str, dict]:
        return {product.value: self.health(product) for product in BinanceProduct}

    async def _fetch_server_time(self, product: BinanceProduct) -> dict:
        if self._fetcher is not None:
            return await self._fetcher(product)
        endpoint = ENDPOINTS[product]
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{endpoint.base_url}{endpoint.time_path}")
            response.raise_for_status()
            return response.json()

    async def _sample(self, product: BinanceProduct) -> TimeSample:
        wall_start_ms = time.time_ns() / 1_000_000
        mono_start_ns = time.monotonic_ns()
        payload = await self._fetch_server_time(product)
        mono_finish_ns = time.monotonic_ns()
        rtt_ms = (mono_finish_ns - mono_start_ns) / 1_000_000
        server_time = payload.get("serverTime")
        if (
            not isinstance(server_time, int)
            or server_time < 1_000_000_000_000
            or server_time >= 100_000_000_000_000
        ):
            raise ValueError("Binance serverTime must be Unix epoch milliseconds")
        midpoint_ms = wall_start_ms + (rtt_ms / 2.0)
        return TimeSample(offset_ms=server_time - midpoint_ms, rtt_ms=rtt_ms, server_time_ms=server_time)

    async def refresh(self, product: BinanceProduct, *, reason: str) -> dict:
        state = self.state(product)
        async with state.lock:
            samples: list[TimeSample] = []
            errors: list[str] = []
            rejected = 0
            for _ in range(self.sample_count):
                try:
                    sample = await self._sample(product)
                    if sample.rtt_ms > self.maximum_rtt_ms:
                        rejected += 1
                        continue
                    samples.append(sample)
                except Exception as exc:
                    errors.append(type(exc).__name__)

            state.last_refresh_reason = reason
            state.valid_samples = len(samples)
            state.rejected_samples = rejected + len(errors)
            if len(samples) < self.minimum_valid_samples:
                state.status = "unsafe"
                state.last_error = (
                    f"only {len(samples)}/{self.sample_count} valid time samples"
                    + (f" ({','.join(errors)})" if errors else "")
                )
                log_event(
                    self._logger, message="binance_time_sync_unsafe", level=logging.ERROR,
                    category="trading", product=product.value, refresh_reason=reason,
                    sync_status=state.status, valid_samples=len(samples), rejected_samples=state.rejected_samples,
                )
                return self.health(product)

            offset = statistics.median(sample.offset_ms for sample in samples)
            offset_mad = statistics.median(abs(sample.offset_ms - offset) for sample in samples)
            median_rtt = statistics.median(sample.rtt_ms for sample in samples)
            now_mono = time.monotonic()
            state.offset_ms = offset
            state.last_rtt_ms = median_rtt
            state.offset_mad_ms = offset_mad
            state.last_sync_monotonic = now_mono
            state.last_sync_wall_ms = int(time.time_ns() / 1_000_000)
            state.last_error = None
            state.status = (
                "synced"
                if abs(offset) <= self.maximum_abs_offset_ms and offset_mad <= self.maximum_offset_mad_ms
                else "unsafe"
            )
            if state.status == "unsafe":
                state.last_error = "measured clock offset or dispersion exceeds configured safety bound"
            log_event(
                self._logger,
                message="binance_time_synchronized" if state.status == "synced" else "binance_time_sync_unsafe",
                level=logging.INFO if state.status == "synced" else logging.ERROR,
                category="trading", product=product.value, measured_offset_ms=round(offset, 3),
                round_trip_ms=round(median_rtt, 3), sample_age_seconds=0.0,
                offset_mad_ms=round(offset_mad, 3),
                refresh_reason=reason, sync_status=state.status,
                valid_samples=len(samples), rejected_samples=state.rejected_samples,
            )
            return self.health(product)

    async def ensure_synced(
        self, product: BinanceProduct, *, reason: str, require_safe: bool
    ) -> dict:
        health = self.health(product)
        if health["status"] != "synced":
            health = await self.refresh(product, reason=reason)
        if require_safe and health["status"] != "synced":
            raise BinanceTimestampUnsafe(
                f"Binance {product.value} timestamp synchronization is {health['status']}; entry blocked"
            )
        return health

    def timestamp_ms(self, product: BinanceProduct, *, require_safe: bool) -> int:
        health = self.health(product)
        if require_safe and health["status"] != "synced":
            raise BinanceTimestampUnsafe(
                f"Binance {product.value} timestamp synchronization is {health['status']}; entry blocked"
            )
        # Sign at the final possible moment; no timezone arithmetic is used.
        return int(time.time_ns() / 1_000_000 + self.state(product).offset_ms)


binance_time = BinanceTimeService()

_background_started = False


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(binance_time.refresh_interval_seconds)
        for product in (BinanceProduct.USD_M_FUTURES, BinanceProduct.SPOT):
            try:
                await binance_time.refresh(product, reason="periodic_refresh")
            except Exception as exc:
                log_event(
                    binance_time._logger, message="binance_time_periodic_refresh_failed",
                    level=logging.ERROR, category="trading", product=product.value,
                    error=type(exc).__name__, sync_status="unsafe",
                )


def start_binance_time_sync() -> None:
    global _background_started
    if _background_started:
        return
    _background_started = True
    asyncio.create_task(_refresh_loop())
