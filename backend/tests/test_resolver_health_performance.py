"""Phase 34: resolver-health performance rework.

Root cause of the original bug: GET /api/analysis/resolver-health did
`base.filter(deadline<=now).all()` - an UNBOUNDED load of every overdue
PredictionLedger row into Python, then classified each one with a per-row
function call. At production scale (~16,100 due/unresolved rows observed
against a real copy of the live database) this made the endpoint take well
over 15-20 seconds. These tests prove the rewrite: exact SQL aggregation for
counts and for any row the resolver has already classified (persisted
unresolved_reason column), a hard-bounded sample for the small residual tail
the resolver hasn't touched yet, and no unbounded query regardless of how
many unresolved rows exist.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

import app.api.analysis as analysis_module
from app.db.session import SessionLocal, engine
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.decision_engine import scheduler as resolver_scheduler

LARGE_FIXTURE_SIZE = 20_000


def _row(pid, symbol, generated, deadline, reference=100.0, timeframe="1m", unresolved_reason=None):
    return PredictionLedger(
        prediction_id=f"perf-{pid}", candidate_id=f"perf-cand-{pid}", decision_id=f"perf-dec-{pid}", user_id="perf-user",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="perf_source",
        source_version="1", symbol=symbol, timeframe=timeframe, direction="LONG", confidence=0.5,
        target_horizon_seconds=int((deadline - generated).total_seconds()), feature_snapshot_hash=f"perf-h-{pid}",
        generated_at=generated, resolution_deadline=deadline, reference_price=reference,
        unresolved_reason=unresolved_reason,
    )


@pytest.fixture(autouse=True)
def _clean():
    yield
    db = SessionLocal()
    try:
        ids = [r.prediction_id for r in db.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like("perf-%"))]
        if ids:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(chunk)).delete(synchronize_session=False)
                db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(chunk)).delete(synchronize_session=False)
            db.commit()
        db.query(MarketCandle).filter(MarketCandle.symbol.like("PERF%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def make_client():
    app = FastAPI()
    app.include_router(analysis_module.router)
    return TestClient(app)


# Realistic multi-timeframe spread (short timeframes dominate real usage) -
# a single-timeframe fixture hides two real regressions this test suite is
# meant to catch: the NOT-IN catch-all partition's cost, and the fact that
# concentrating all rows in one partition doesn't exercise the multi-
# partition code path the way production data actually does.
_TF_WEIGHTS = [("1m", 30), ("3m", 15), ("5m", 20), ("15m", 15), ("30m", 5), ("1h", 5), ("4h", 3), ("1d", 2), ("1w", 1)]


def _seed_large_backlog(n, reason_mix=True):
    """n overdue, unresolved PredictionLedger rows spread across realistic
    timeframes. When reason_mix is True, a realistic split: most already
    classified by the resolver (persisted unresolved_reason, the exact/fast
    path), a smaller residual tail still NULL (the sampled path) - mirrors
    what resolver.py actually produces (see resolver.py:159-231, every
    reason but the live-computed ones is persisted the first time the
    resolver looks at a row)."""
    now = datetime.now(timezone.utc)
    total_weight = sum(w for _, w in _TF_WEIGHTS)
    rows = []
    i = 0
    for tf, weight in _TF_WEIGHTS:
        count = max(1, int(n * weight / total_weight))
        for j in range(count):
            deadline = now - timedelta(hours=2, minutes=j % 60)
            generated = deadline - timedelta(minutes=5)
            reason = None
            if reason_mix:
                # ~85% already classified by a prior resolver cycle, ~15% still untouched
                if j % 20 < 17:
                    reason = ["awaiting_future_candle", "resolver_delayed", "market_data_gap", "permanent_data_gap"][j % 4]
            rows.append(_row(i, "PERFUSDT", generated, deadline, timeframe=tf, unresolved_reason=reason))
            i += 1
    db = SessionLocal()
    try:
        for k in range(0, len(rows), 2000):
            db.bulk_save_objects(rows[k:k + 2000])
            db.commit()
    finally:
        db.close()


def test_endpoint_handles_20000_plus_unresolved_predictions_within_the_hard_bound(monkeypatch):
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    _seed_large_backlog(LARGE_FIXTURE_SIZE)
    client = make_client()

    started = time.monotonic()
    r = client.get("/api/analysis/resolver-health")
    elapsed = time.monotonic() - started

    assert r.status_code == 200
    assert elapsed < 3.0, f"resolver-health took {elapsed:.2f}s against {LARGE_FIXTURE_SIZE} unresolved rows - exceeds the 3s hard bound"
    body = r.json()
    # The weighted timeframe split rounds each bucket down, so the actual
    # insert count is slightly below LARGE_FIXTURE_SIZE - still comfortably
    # "20,000+" in spirit; the point is the endpoint handling this scale
    # within the hard bound above, not an exact row count.
    assert body["overdue_count"] >= LARGE_FIXTURE_SIZE * 0.9
    # sum of every bucket (including the honest unclassified_pending_sample
    # bucket, if sampling didn't cover everything) must equal overdue_count -
    # nothing is silently dropped.
    assert sum(body["unresolved_reason_counts"].values()) == body["overdue_count"]


def test_counts_remain_correct_against_a_known_fixture(monkeypatch):
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        # 5 clearly overdue (grace period elapsed), 3 due-but-within-grace, 2 not due
        for i in range(5):
            db.add(_row(f"kfo-{i}", "PERFUSDT", now - timedelta(hours=1), now - timedelta(minutes=30), timeframe="5m", unresolved_reason="resolver_delayed"))
        for i in range(3):
            db.add(_row(f"kfg-{i}", "PERFUSDT", now - timedelta(seconds=20), now - timedelta(seconds=10), timeframe="5m"))
        for i in range(2):
            db.add(_row(f"kfn-{i}", "PERFUSDT", now, now + timedelta(hours=1), timeframe="5m"))
        db.commit()
    finally:
        db.close()

    client = make_client()
    r = client.get("/api/analysis/resolver-health")
    body = r.json()
    assert body["overdue_count"] >= 5
    assert body["due_count"] >= 8
    assert body["not_due_count"] >= 2
    assert body["unresolved_reason_counts"].get("resolver_delayed", 0) >= 5


def test_no_unbounded_query_regardless_of_backlog_size(monkeypatch):
    """The real regression proof: query COUNT issued by the endpoint must
    not grow proportionally with the number of unresolved rows. The old
    code's .all() meant SQLAlchemy hydrated one row's worth of work per
    unresolved prediction; the new code issues a small, fixed number of
    aggregate queries plus at most one bounded SELECT."""
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    _seed_large_backlog(LARGE_FIXTURE_SIZE)
    client = make_client()

    queries = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        r = client.get("/api/analysis/resolver-health")
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert r.status_code == 200
    # A fixed ceiling of ~2-3 queries per timeframe partition (11 possible
    # partitions), never one-per-row - the exact count isn't the point, the
    # CEILING independent of row count is: one-per-row would be 20,000+.
    assert len(queries) < 40, f"resolver-health issued {len(queries)} SQL statements for {LARGE_FIXTURE_SIZE} rows - looks unbounded"
    # No SELECT of the full ORM row set without a LIMIT: every SELECT that
    # touches prediction_ledger must carry either an aggregate function or
    # an explicit LIMIT clause.
    for sql in queries:
        upper = sql.upper()
        if "FROM PREDICTION_LEDGER" in upper.replace('"', '') and "SELECT" in upper:
            has_aggregate = any(fn in upper for fn in ("COUNT(", "MIN(", "MAX(", "SUM("))
            has_limit = " LIMIT " in upper
            assert has_aggregate or has_limit, f"unbounded prediction_ledger query found: {sql}"


def test_provider_errors_are_represented_safely_and_are_never_sampled(monkeypatch):
    """provider_unavailable/resolver_error are always persisted by the
    resolver itself (resolver.py:123,170) so they're always in the exact
    GROUP BY path - the provider_status/provider_error signal must be
    accurate even when the residual sampled bucket is large."""
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_row("prov-1", "PERFUSDT", now - timedelta(hours=1), now - timedelta(minutes=30), timeframe="5m", unresolved_reason="provider_unavailable"))
        # a large NULL-reason tail that would exceed the sample bound in a
        # smaller test - proves the provider signal isn't diluted by it
        for i in range(50):
            db.add(_row(f"prov-null-{i}", "PERFUSDT", now - timedelta(hours=1), now - timedelta(minutes=30), timeframe="5m"))
        db.commit()
    finally:
        db.close()

    client = make_client()
    r = client.get("/api/analysis/resolver-health")
    body = r.json()
    assert body["provider_status"] == "error"
    assert "unreachable" in body["provider_error"]
    assert body["unresolved_reason_counts"]["provider_unavailable"] == 1
