"""GET /api/trading/pipeline/current - field completeness, timeframe
resolution correctness, and that a fully-approved decision reports the
same identifiers whether queried once or twice (restart/duplicate-safe)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.pipeline as pipeline_module
from app.core.config import settings
from app.db.models import ExecutionIntentAudit, Trade, TradingHorizonDecision, TradingHorizonTimeframeLink
from app.db.session import SessionLocal
from app.trading import modes

from tests.test_horizon_authority import persisted_decision, setup_user

REQUIRED_FIELDS = {
    "decision_id", "cycle_id", "authority_id", "execution_request_id", "order_id", "position_id",
    "engine", "symbol", "timeframe", "signal", "candidate_direction", "actionable", "confidence",
    "confidence_threshold", "confidence_passed", "trade_levels_valid", "edge_supported", "expected_edge",
    "edge_block_reason", "authority_status", "authority_block_reason", "risk_allowed", "risk_reason",
    "execution_mode", "execution_status", "current_stage", "completed_stages", "pending_stage",
    "final_block_reason", "created_at", "updated_at", "next_evaluation_at", "stale",
}


def make_client():
    app = FastAPI()
    app.include_router(pipeline_module.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    setup_user(settings.admin_username)
    modes.set_mode(modes.MODE_PAPER)
    db = SessionLocal()
    db.query(ExecutionIntentAudit).delete()
    db.query(Trade).delete()
    db.commit()
    db.close()
    yield


def test_response_has_every_required_field():
    """No persisted authority exists yet, so the resolver falls back to a
    live side-effect-free preview (or a configured/default profile) -
    exactly the plan's 3-tier resolution order. Whatever it resolves to,
    every field the unified API contract promises must be present."""
    client = make_client()
    r = client.get("/api/trading/pipeline/current", params={"symbol": "BTCUSDT"})
    assert r.status_code == 200
    body = r.json()
    assert REQUIRED_FIELDS <= set(body.keys())
    assert body["timeframe_source"] in ("persisted_authority", "preview_no_authority_yet",
                                        "configured_profile", "default_profile")
    assert body["authority_id"] is None
    assert body["execution_request_id"] is None
    assert body["order_id"] is None


def test_fully_approved_decision_resolves_persisted_authority_timeframe():
    decision, _ = persisted_decision(user=settings.admin_username, return_inputs=True)
    db = SessionLocal()
    link = db.query(TradingHorizonTimeframeLink).filter_by(
        horizon_decision_id=decision["profile_decision_id"], role="execution").first()
    from app.db.models import ActiveDriveDecision
    row = db.get(ActiveDriveDecision, link.decision_id)
    payload = dict(row.decision_payload or {})
    payload["recommended_stop"] = 100.0
    payload["recommended_target"] = 120.0
    row.decision_payload = payload
    db.commit()
    db.close()

    client = make_client()
    r = client.get("/api/trading/pipeline/current", params={"symbol": "BTCUSDT"})
    assert r.status_code == 200
    body = r.json()
    assert body["timeframe"] == decision["execution_timeframe"]
    assert body["timeframe_source"] == "persisted_authority"
    assert body["authority_id"] == decision["profile_decision_id"]
    assert body["execution_status"] == "approved_for_paper_execution"
    assert body["candidate_direction"] == decision["direction"]
    assert body["actionable"] is True


def test_repeated_reads_of_the_same_authority_are_identical_no_duplication():
    decision, _ = persisted_decision(user=settings.admin_username, return_inputs=True)
    db = SessionLocal()
    link = db.query(TradingHorizonTimeframeLink).filter_by(
        horizon_decision_id=decision["profile_decision_id"], role="execution").first()
    from app.db.models import ActiveDriveDecision
    row = db.get(ActiveDriveDecision, link.decision_id)
    payload = dict(row.decision_payload or {})
    payload["recommended_stop"] = 100.0
    payload["recommended_target"] = 120.0
    row.decision_payload = payload
    db.commit()
    db.close()

    client = make_client()
    first = client.get("/api/trading/pipeline/current", params={"symbol": "BTCUSDT"}).json()
    second = client.get("/api/trading/pipeline/current", params={"symbol": "BTCUSDT"}).json()
    assert first["authority_id"] == second["authority_id"] == decision["profile_decision_id"]
    assert first["execution_status"] == second["execution_status"]
    db = SessionLocal()
    assert db.query(TradingHorizonDecision).filter_by(id=decision["profile_decision_id"]).count() == 1
    db.close()
