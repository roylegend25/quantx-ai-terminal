"""Centralized Binance data snapshot service (Phase 29).

Root cause of "Rate limited by Binance": many independent backend
endpoints (portfolio summary/positions/orders/trades/income, the
protection watchdog, the margin calculator, Binance risk-status,
diagnostics) each read account/balances/positions/orders/income straight
off BinanceFuturesClient with little or no shared caching, so five
browser tabs open on five different pages (or even one tab's normal
10s-interval polling loops) could add up to far more signed Binance
round trips per minute than a single account's rate budget tolerates.

This module is the ONE place that owns those repeated reads. Every
endpoint above should call through here instead of the client directly,
and get a single shared, TTL-cached, single-flight-coalesced answer: if
five callers ask for the account bundle inside the same TTL window, only
the first actually reaches Binance - the other four await the same
in-flight call (or its just-stored result) via one asyncio.Lock per
section.

Never wipes good data: if a refresh fails (rate limited or otherwise),
the LAST KNOWN GOOD value for that section is returned with its `stale`
flag set, rather than an empty/zeroed value - a live position must never
appear to vanish from the UI just because a refresh was rate-limited
(BINANCE_ENABLE_STALE_CACHE_ON_RATE_LIMIT). Only a client that has never
successfully returned data raises, exactly like the plain client calls
this replaces - there is nothing "good" to fall back to yet.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.core.config import settings


def _safe_error(e: Exception) -> str:
    from app.trading.execution_router import _safe_error as safe_error
    return safe_error(e)


@dataclass
class _Section:
    """One independently TTL-cached, single-flight-coalesced piece of the
    snapshot (account bundle, balances, open orders, income, ...)."""

    ttl: float
    value: object = None
    fetched_at: float = 0.0
    client_id: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stale: bool = False
    error: str | None = None
    retry_after_seconds: float | None = None

    def fresh_for(self, client) -> bool:
        return (
            self.value is not None
            and self.client_id == id(client)
            and time.time() - self.fetched_at < self.ttl
        )

    def age_seconds(self) -> float | None:
        return round(time.time() - self.fetched_at, 1) if self.fetched_at else None

    def invalidate(self) -> None:
        """Forces the next fresh_for() check to fail regardless of TTL,
        without discarding the last-known-good value (still readable via
        `.value` if a caller wants stale-but-cheap data) - see
        BinanceSnapshotService.invalidate_after_protection_mutation."""
        self.fetched_at = 0.0

    def meta(self) -> dict:
        return {
            "stale": self.stale,
            "error": self.error,
            "retry_after_seconds": self.retry_after_seconds,
            "age_seconds": self.age_seconds(),
        }


class BinanceSnapshotService:
    def __init__(self):
        self._account = _Section(ttl=settings.binance_snapshot_ttl_seconds)
        self._balances = _Section(ttl=settings.binance_snapshot_ttl_seconds)
        self._orders = _Section(ttl=settings.binance_orders_ttl_seconds)
        self._income = _Section(ttl=settings.binance_income_ttl_seconds)
        self._trades = _Section(ttl=settings.binance_income_ttl_seconds)

    async def _get_or_fetch(self, section: _Section, client, fetch, *, force_refresh: bool):
        if not force_refresh and section.fresh_for(client):
            return section.value
        async with section.lock:
            # Single-flight: re-check inside the lock in case a concurrent
            # caller already refreshed this section while we were waiting
            # on it - the thundering-herd case this whole service exists
            # to prevent collapses to exactly one Binance round trip.
            if not force_refresh and section.fresh_for(client):
                return section.value
            try:
                value = await fetch()
            except Exception as e:
                message = _safe_error(e)
                retry_after = getattr(e, "retry_after_seconds", None)
                can_serve_stale = (
                    settings.binance_enable_stale_cache_on_rate_limit
                    and section.client_id == id(client)
                    and section.value is not None
                )
                if can_serve_stale:
                    section.stale = True
                    section.error = message
                    section.retry_after_seconds = retry_after
                    return section.value
                section.error = message
                section.retry_after_seconds = retry_after
                raise
            section.value = value
            section.fetched_at = time.time()
            section.client_id = id(client)
            section.stale = False
            section.error = None
            section.retry_after_seconds = None
            return value

    # ------------------------------------------------------ shared reads

    async def get_account_bundle(self, client, force_refresh: bool = False) -> tuple:
        """(account, positions, daily_pnl) - drop-in for the shape
        app.api.portfolio.get_account_snapshot has always returned, now
        backed by the shared cache used by every other Binance-reading
        endpoint too."""

        # Priority is NOT passed explicitly here - each client method already
        # defaults to the right priority level (HIGH for account/positions,
        # see binance_futures_client.py), which also keeps this service
        # compatible with every test double across the codebase that
        # duck-types these methods without a priority parameter at all.
        async def fetch():
            account = await client.get_account_info()
            positions = await client.get_positions()
            daily_pnl = await client.get_daily_realized_pnl()
            return (account, positions, daily_pnl)

        return await self._get_or_fetch(self._account, client, fetch, force_refresh=force_refresh)

    async def get_balances(self, client, force_refresh: bool = False) -> list:
        async def fetch():
            return await client.get_balances()

        return await self._get_or_fetch(self._balances, client, fetch, force_refresh=force_refresh)

    async def get_open_orders(self, client, force_refresh: bool = False) -> list:
        async def fetch():
            return await client.get_open_orders()

        return await self._get_or_fetch(self._orders, client, fetch, force_refresh=force_refresh)

    async def get_income(self, client, limit: int = 50, force_refresh: bool = False) -> list:
        async def fetch():
            return await client.get_income_history(limit=limit)

        return await self._get_or_fetch(self._income, client, fetch, force_refresh=force_refresh)

    async def get_trades(self, client, symbols: list[str], force_refresh: bool = False) -> list:
        """Recent trade history across every allowed symbol - LOW priority
        historical data (section 15), cached at the same TTL as income
        since both are "how did we do recently" reads, never needed
        second-by-second. A per-symbol failure is dropped rather than
        failing the whole call (matches the previous per-endpoint
        behavior in app.api.portfolio.binance_trades)."""

        async def fetch():
            result = []
            successes = 0
            last_error: Exception | None = None
            for symbol in symbols:
                try:
                    result.extend(await client.get_trade_history(symbol, limit=50))
                    successes += 1
                except Exception as e:
                    last_error = e
                    continue  # one bad symbol must never empty the whole view
            if symbols and successes == 0:
                raise last_error
            return result

        return await self._get_or_fetch(self._trades, client, fetch, force_refresh=force_refresh)

    # -------------------------------------------------------- introspection

    def section_meta(self) -> dict:
        """Staleness/error/age per section, read AFTER calling the get_*
        methods above (they populate this as a side effect) - used by the
        /api/binance/snapshot endpoint and diagnostics. Never contains a
        raw Binance payload, only the same safe error classification the
        rest of the app already uses."""
        return {
            "account": self._account.meta(),
            "balances": self._balances.meta(),
            "orders": self._orders.meta(),
            "income": self._income.meta(),
            "trades": self._trades.meta(),
        }

    def any_stale(self) -> bool:
        return any(s.stale for s in (self._account, self._balances, self._orders, self._income, self._trades))

    def invalidate_after_protection_mutation(self) -> None:
        """Phase 30: called immediately after any confirmed protection
        change (place/edit/repair/cancel a TP or SL) so the very next
        risk-gate/portfolio/watchdog read sees fresh positions/orders
        instead of waiting out BINANCE_SNAPSHOT_TTL_SECONDS /
        BINANCE_ORDERS_TTL_SECONDS. This does NOT make an extra Binance
        call itself - it only marks the account and open-orders sections
        expired, so the next caller (whoever that is) does one single-
        flight-coalesced fresh fetch, exactly like a normal cache miss."""
        self._account.invalidate()
        self._orders.invalidate()

    def reset(self) -> None:
        """Test-only hook: forget every cached section."""
        self.__init__()


# One process-wide instance, imported by app.api.portfolio, the protection
# watchdog, the margin calculator and the new /api/binance/snapshot router -
# never construct a second one, or the whole point (one shared cache) is lost.
snapshot_service = BinanceSnapshotService()
