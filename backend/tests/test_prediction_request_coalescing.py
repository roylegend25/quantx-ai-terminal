"""Concurrent GET /api/prediction/{symbol} requests for the same
(user, engine, symbol, interval) key that land after the cache has expired
must share one in-flight compute+persist, not race independent computations
that each persist their own ActiveDriveDecision row for the same cycle.

Without coalescing this was both a correctness problem (Section 3: more than
one persisted "authoritative" V2 decision could exist for the same cycle)
and a latency problem (Section 4: concurrent dashboard reads produced their
own 15-30s cold computations instead of sharing one)."""
import asyncio
import time

import httpx
import pytest

from app.api import prediction as prediction_module
from tests.test_prediction_multi_symbol import _FakeAsyncClient, _fake_get_context


def test_concurrent_requests_for_same_key_share_one_compute_and_persist(monkeypatch):
    prediction_module._prediction_cache.clear()
    prediction_module._prediction_inflight.clear()

    persist_calls = {"count": 0}
    original_persist = prediction_module.persist_engine_decision

    def counting_persist(*args, **kwargs):
        persist_calls["count"] += 1
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(prediction_module, "persist_engine_decision", counting_persist)
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)

    async def run():
        return await asyncio.gather(*[
            prediction_module.compute_and_persist_prediction("BTCUSDT", current_user="coalesce-user")
            for _ in range(8)
        ])

    responses = asyncio.run(run())

    assert persist_calls["count"] == 1, "8 concurrent requests for the same key must persist exactly one decision"
    decision_ids = {r["prediction"]["decision_id"] for r in responses}
    assert len(decision_ids) == 1, "all concurrent callers must observe the same decision_id"
    assert not prediction_module._prediction_inflight, "the in-flight entry must be cleared once the shared compute finishes"


def test_sequential_requests_after_cache_expiry_each_recompute_independently(monkeypatch):
    """Coalescing only applies to genuinely concurrent callers - a request
    made after the previous result has already aged out of the cache must
    still recompute (never serve a stale response forever)."""
    prediction_module._prediction_cache.clear()
    prediction_module._prediction_inflight.clear()

    persist_calls = {"count": 0}
    original_persist = prediction_module.persist_engine_decision

    def counting_persist(*args, **kwargs):
        persist_calls["count"] += 1
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(prediction_module, "persist_engine_decision", counting_persist)
    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)

    asyncio.run(prediction_module.compute_and_persist_prediction("BTCUSDT", current_user="coalesce-user-2"))
    assert persist_calls["count"] == 1

    # Force the cache entry to look expired without waiting the full 60s TTL.
    for key in list(prediction_module._prediction_cache):
        prediction_module._prediction_cache[key]["computed_at"] -= (prediction_module.PREDICTION_CACHE_TTL_SECONDS + 5) * 1000

    asyncio.run(prediction_module.compute_and_persist_prediction("BTCUSDT", current_user="coalesce-user-2"))
    assert persist_calls["count"] == 2


def test_provider_failure_skips_live_retry_during_cooldown_then_retries_after(monkeypatch):
    """During the cooldown window after an observed live-provider failure,
    the next call must go straight to cached candles instead of paying
    another full timeout - but once the cooldown elapses, a live attempt
    must resume (never permanently pinned to cached data)."""
    class FailingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("offline", request=httpx.Request("GET", "https://fake.invalid"))

    attempts = {"count": 0}

    class CountingFailingClient(FailingClient):
        async def get(self, *args, **kwargs):
            attempts["count"] += 1
            return await super().get(*args, **kwargs)

    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", lambda **kwargs: CountingFailingClient())

    candles1, provenance1 = asyncio.run(prediction_module._fetch_candles_with_fallback("BTCUSDT", "5m", 50))
    assert provenance1["source"] == "cached_db" and attempts["count"] == 1

    candles2, provenance2 = asyncio.run(prediction_module._fetch_candles_with_fallback("BTCUSDT", "5m", 50))
    assert provenance2["provider_error"] == "provider_recently_failed_skipping_live_attempt"
    assert attempts["count"] == 1, "a call inside the cooldown window must not attempt the network again"

    prediction_module._provider_last_failure_at = time.time() - prediction_module._PROVIDER_FAILURE_COOLDOWN_SECONDS - 1
    asyncio.run(prediction_module._fetch_candles_with_fallback("BTCUSDT", "5m", 50))
    assert attempts["count"] == 2, "once the cooldown elapses, a live attempt must resume"
