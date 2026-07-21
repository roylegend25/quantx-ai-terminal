"""Phase 33: GET /api/analysis/resolver-health - global resolver health,
due/overdue counts, and structured unresolved-reason breakdown."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.analysis as analysis_module
from app.db.session import SessionLocal
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.decision_engine import scheduler as resolver_scheduler


def _row(pid, symbol, generated, deadline, reference=100.0, timeframe="1m"):
    return PredictionLedger(
        prediction_id=f"rh-{pid}", candidate_id=f"rh-cand-{pid}", decision_id=f"rh-dec-{pid}", user_id="rh-user",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="rh_source",
        source_version="1", symbol=symbol, timeframe=timeframe, direction="LONG", confidence=0.5,
        target_horizon_seconds=int((deadline - generated).total_seconds()), feature_snapshot_hash=f"h-{pid}",
        generated_at=generated, resolution_deadline=deadline, reference_price=reference,
    )


@pytest.fixture(autouse=True)
def _clean():
    yield
    db = SessionLocal()
    try:
        ids = [r.prediction_id for r in db.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like("rh-%"))]
        if ids:
            db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.query(MarketCandle).filter(MarketCandle.symbol.like("RH%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def make_client():
    app = FastAPI()
    app.include_router(analysis_module.router)
    return TestClient(app)


def test_resolver_health_reports_due_overdue_and_reasons(monkeypatch):
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": "2026-07-18T00:00:00+00:00", "last_batch_at": "2026-07-18T00:00:00+00:00",
        "last_success": "2026-07-18T00:00:00+00:00", "last_resolved": 3, "last_error": None,
        "next_run": "2026-07-18T00:01:00+00:00",
    })
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        # overdue: due long enough ago that its own horizon-bar grace period has elapsed
        db.add(_row("overdue", "RHAUSDT", now - timedelta(hours=1), now - timedelta(minutes=30)))
        # due but not yet overdue (within grace)
        db.add(_row("due-not-overdue", "RHBUSDT", now - timedelta(seconds=10), now - timedelta(seconds=5)))
        # not due yet
        db.add(_row("not-due", "RHCUSDT", now, now + timedelta(hours=1)))
        db.commit()

        client = make_client()
        r = client.get("/api/analysis/resolver-health")
        assert r.status_code == 200
        body = r.json()
        assert body["resolver_running"] is True
        assert body["due_count"] >= 2
        assert body["not_due_count"] >= 1
        assert body["overdue_count"] >= 1
        assert body["oldest_overdue_at"] is not None
        assert body["oldest_overdue_age_seconds"] is not None and body["oldest_overdue_age_seconds"] > 0
        assert isinstance(body["unresolved_reason_counts"], dict)
        assert body["last_success_at"] == "2026-07-18T00:00:00+00:00"
    finally:
        db.close()


def test_resolver_health_reports_not_running(monkeypatch):
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": False, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    client = make_client()
    r = client.get("/api/analysis/resolver-health")
    assert r.status_code == 200
    body = r.json()
    assert body["resolver_running"] is False
    assert body["provider_status"] == "unknown"


def test_resolver_health_never_fabricates_provider_error_without_evidence(monkeypatch):
    monkeypatch.setattr(resolver_scheduler, "STATUS", {
        "running": True, "last_run": None, "last_batch_at": None, "last_success": None,
        "last_resolved": 0, "last_error": None, "next_run": None,
    })
    client = make_client()
    r = client.get("/api/analysis/resolver-health")
    body = r.json()
    assert body["provider_error"] is None
    assert body["provider_status"] == "ok"
