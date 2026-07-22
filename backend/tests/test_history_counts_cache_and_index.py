"""Two follow-on fixes found while verifying the N+1 batching fix against
production-scale data (269k+ prediction_ledger rows): performance_batch()'s
query needs its own index (it deliberately omits source_name, unlike the
pre-existing per-candidate index), and _history_counts()'s unscoped
global_resolved count became the largest remaining cost inside evaluate()
once the N+1 was fixed - it's purely diagnostic display data (never a
gating input), so it's now cached with a short TTL."""
import time

from sqlalchemy import inspect

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.decision_engine.v2 import _history_counts, _history_counts_cache


def test_perf_batch_index_exists():
    inspector = inspect(engine)
    index_names = {idx["name"] for idx in inspector.get_indexes("prediction_ledger")}
    assert "ix_prediction_ledger_perf_batch" in index_names


def test_history_counts_is_cached_within_ttl():
    _history_counts_cache.clear()
    db = SessionLocal()
    try:
        first = _history_counts(db, settings.admin_username, "cache-test-symbol", "1h", "TRENDING")
        key = (settings.admin_username, "cache-test-symbol", "1h", "TRENDING")
        assert key in _history_counts_cache
        cached_at, cached_value = _history_counts_cache[key]
        assert cached_value == first

        # A second call within the TTL must return the exact same object
        # from cache, not recompute (we can't easily assert "no query ran"
        # here without duplicating the query-count harness, so instead
        # prove the cache entry's timestamp does not advance).
        second = _history_counts(db, settings.admin_username, "cache-test-symbol", "1h", "TRENDING")
        assert second == first
        assert _history_counts_cache[key][0] == cached_at
    finally:
        db.close()
        _history_counts_cache.clear()


def test_history_counts_cache_expires_after_ttl(monkeypatch):
    import app.decision_engine.v2 as v2_module
    monkeypatch.setattr(v2_module, "_HISTORY_COUNTS_CACHE_TTL_SECONDS", 0.05)
    v2_module._history_counts_cache.clear()
    db = SessionLocal()
    try:
        v2_module._history_counts(db, settings.admin_username, "cache-expiry-symbol", "1h", "TRENDING")
        key = (settings.admin_username, "cache-expiry-symbol", "1h", "TRENDING")
        first_ts = v2_module._history_counts_cache[key][0]
        time.sleep(0.1)
        v2_module._history_counts(db, settings.admin_username, "cache-expiry-symbol", "1h", "TRENDING")
        assert v2_module._history_counts_cache[key][0] > first_ts
    finally:
        db.close()
        v2_module._history_counts_cache.clear()
