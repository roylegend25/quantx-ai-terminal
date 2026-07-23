"""GET /api/predictions/resolver/health and /lifecycle-health are full-scan
aggregations over prediction_ledger (290k+ rows and growing) - several
seconds per call regardless of query tuning, since there is no indexed
"is this row resolved" column. Serving a short-lived cached result at the
route layer fixed a real production incident: the deploy script's health
check timed out repeatedly against a freshly-swapped, otherwise-healthy
container because concurrent dashboard load pushed each call past its
timeout. This tests the _TTLCache helper directly (app/api/prediction_results.py)
- fast, no DB fixture needed."""
from app.api.prediction_results import _TTLCache


def test_ttl_cache_reuses_result_within_ttl():
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    cache = _TTLCache(ttl_seconds=60.0)
    first = cache.get_or_compute(compute)
    second = cache.get_or_compute(compute)
    assert first == second == {"n": 1}
    assert len(calls) == 1


def test_ttl_cache_recomputes_after_expiry():
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    cache = _TTLCache(ttl_seconds=0.0)
    first = cache.get_or_compute(compute)
    second = cache.get_or_compute(compute)
    assert first == {"n": 1}
    assert second == {"n": 2}
    assert len(calls) == 2


def test_ttl_cache_first_call_always_computes():
    cache = _TTLCache(ttl_seconds=60.0)
    result = cache.get_or_compute(lambda: {"ok": True})
    assert result == {"ok": True}
