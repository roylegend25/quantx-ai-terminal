"""Phase 34: a single, additive metadata envelope for market-data API
responses. Every field here answers a question a trader/operator actually
needs to trust a number: where did this come from, when was it fetched, how
old is the underlying market data, how long did the round trip take, is
this a cached/stale read, did anything go wrong, and - if this provider is
degraded - what (if anything) served the response instead.

Additive by design: callers merge this dict's keys alongside their existing
response shape (`{**payload, **market_meta(...)}`), so no existing consumer
of an endpoint's current fields breaks. Never fabricates a value it can't
observe - every optional argument defaults to None/False rather than a
guessed number.
"""

from __future__ import annotations

import time
from typing import Any


def market_meta(
    *,
    source: str,
    source_type: str,
    market_timestamp: float | None = None,
    fetched_at: float | None = None,
    latency_ms: float | None = None,
    stale: bool = False,
    error: str | None = None,
    fallback_source: str | None = None,
) -> dict[str, Any]:
    """
    source: the concrete provider that actually answered this request
      (e.g. "binance_futures", "hyperliquid_ws", "binance_estimated").
    source_type: the category of that provider ("exchange_rest",
      "exchange_ws", "derived_estimate", "cache").
    market_timestamp: when the underlying market event/snapshot itself is
      timestamped, in epoch seconds - distinct from fetched_at, which is
      when THIS server completed the read. None when the upstream payload
      carries no timestamp of its own (never backfilled with fetched_at,
      that would misrepresent an unknown as a known value).
    fetched_at: epoch seconds this server obtained the data. Defaults to
      "now" at call time if not supplied.
    latency_ms: measured round-trip time for the upstream call, when the
      caller timed it.
    stale: True when this is a cached/last-known-good response rather than
      a fresh read (e.g. served during a rate limit).
    error: the observed failure reason, only ever set from a real caught
      exception - never invented to explain an otherwise-successful call.
    fallback_source: name of a secondary provider that answered instead of
      the primary one, only when a real fallback actually occurred.
    """
    return {
        "source": source,
        "source_type": source_type,
        "market_timestamp": market_timestamp,
        "fetched_at": fetched_at if fetched_at is not None else time.time(),
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "stale": stale,
        "error": error,
        "fallback_source": fallback_source,
    }
