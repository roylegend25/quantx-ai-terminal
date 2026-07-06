"""In-process TTL cache for provider responses.

Keeps repeated dashboard/feature-engine reads from hammering the free public
APIs (Binance weight limits, alternative.me, CoinGecko). Values are cached
with the wall-clock time they were fetched so callers can also ask "is this
still fresh enough to trade on?" - the safety rule is that stale cached data
may be *shown* but never silently treated as live.
"""

import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, default_ttl_seconds: float = 60.0, max_entries: int = 2048):
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl_seconds
        self._max_entries = max_entries

    def get(self, key: str, ttl_seconds: float | None = None) -> Any | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        fetched_at, value = hit
        if time.time() - fetched_at > (ttl_seconds if ttl_seconds is not None else self._default_ttl):
            return None
        return value

    def age_seconds(self, key: str) -> float | None:
        hit = self._store.get(key)
        return time.time() - hit[0] if hit else None

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max_entries:
            # drop the oldest entry - simple and good enough for this workload
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.time(), value)

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
        ttl_seconds: float | None = None,
        allow_stale_on_error: bool = True,
        max_stale_seconds: float = 900.0,
    ) -> tuple[Any, bool]:
        """Returns (value, is_stale). On provider failure, a previously cached
        value younger than max_stale_seconds is returned flagged stale rather
        than crashing the caller; anything older re-raises so bad data is
        never silently used."""
        fresh = self.get(key, ttl_seconds)
        if fresh is not None:
            return fresh, False
        try:
            value = await fetcher()
        except Exception:
            if allow_stale_on_error:
                age = self.age_seconds(key)
                if age is not None and age <= max_stale_seconds:
                    return self._store[key][1], True
            raise
        self.set(key, value)
        return value, False


cache = TTLCache()
