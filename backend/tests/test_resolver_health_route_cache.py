"""GET /api/predictions/resolver/health and /lifecycle-health are full-scan
aggregations over prediction_ledger (290k+ rows and growing) - several
seconds per call regardless of query tuning, since there is no indexed
"is this row resolved" column. Serving a short-lived cached result at the
route layer fixed a real production incident: the deploy script's health
check timed out repeatedly against a freshly-swapped, otherwise-healthy
container because concurrent dashboard load pushed each call past its
timeout. This tests the _TTLCache helper directly (app/api/prediction_results.py)
- fast, no DB fixture needed."""
import app.api.prediction_results as pr
from app.api.prediction_results import _TTLCache


def _fake_progress(total_overdue, oldest_overdue_age_seconds, last_error=None):
    return {
        "total_due": 0, "total_overdue": total_overdue, "btc": {"due": 0, "overdue": 0},
        "eth": {"due": 0, "overdue": 0}, "resolved_this_run": 0, "failed_this_run": 0,
        "remaining": 0, "processed_total": 0, "resolved_total": 0, "delayed_total": 0,
        "permanently_failed_total": 0, "primary_source_resolutions": 0, "fallback_resolutions": 0,
        "provider_disagreement_count": 0, "oldest_overdue_age_seconds": oldest_overdue_age_seconds,
        "last_run": None, "last_success": None, "last_error": last_error, "estimated_completion": None,
    }


def test_resolver_health_zero_backlog_is_healthy(monkeypatch):
    """Regression: with total_overdue == 0, oldest_overdue_age_seconds is
    None (nothing overdue to time) - this used to make `healthy` false for
    the single best state the resolver can be in, found live right after a
    deploy fully cleared the backlog."""
    monkeypatch.setattr(pr, "_resolver_health_cache", _TTLCache(ttl_seconds=60.0))
    monkeypatch.setattr(pr, "_compute_catchup_progress", lambda: _fake_progress(0, None))
    result = pr.resolver_health()
    assert result["healthy"] is True


def test_resolver_health_recent_overdue_is_healthy(monkeypatch):
    monkeypatch.setattr(pr, "_resolver_health_cache", _TTLCache(ttl_seconds=60.0))
    monkeypatch.setattr(pr, "_compute_catchup_progress", lambda: _fake_progress(5, 60.0))
    result = pr.resolver_health()
    assert result["healthy"] is True


def test_resolver_health_stale_overdue_is_unhealthy(monkeypatch):
    monkeypatch.setattr(pr, "_resolver_health_cache", _TTLCache(ttl_seconds=60.0))
    monkeypatch.setattr(pr, "_compute_catchup_progress", lambda: _fake_progress(5, 90000.0))
    result = pr.resolver_health()
    assert result["healthy"] is False


def test_resolver_health_last_error_is_unhealthy(monkeypatch):
    monkeypatch.setattr(pr, "_resolver_health_cache", _TTLCache(ttl_seconds=60.0))
    monkeypatch.setattr(pr, "_compute_catchup_progress", lambda: _fake_progress(0, None, last_error="boom"))
    result = pr.resolver_health()
    assert result["healthy"] is False


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
