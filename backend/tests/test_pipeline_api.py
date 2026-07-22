"""GET /api/trading/pipeline/current - field completeness, timeframe
resolution correctness, and that a fully-approved decision reports the
same identifiers whether queried once or twice (restart/duplicate-safe).

Trading Horizon removal: timeframe resolution is now a pure, static
configuration lookup (app.decision_engine.profiles) - there is no more
"persisted_authority" vs "preview" distinction, since there is no live
Horizon evaluation cascade to fall back to."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.pipeline as pipeline_module
from app.core.config import settings
from app.db.models import ActiveDriveDecision, ActiveDriveDecisionConsumption, ExecutionIntentAudit, Trade, UserBotSetting
from app.db.session import SessionLocal
from app.decision_engine.execution_gate import finalize_decision_for_execution
from app.decision_engine.profiles import resolve_execution_timeframe
from app.trading import modes

REQUIRED_FIELDS = {
    "decision_id", "cycle_id", "authority_id", "execution_request_id", "order_id", "position_id",
    "engine", "symbol", "timeframe", "signal", "candidate_direction", "actionable", "confidence",
    "confidence_threshold", "confidence_passed", "trade_levels_valid", "edge_supported", "expected_edge",
    "edge_block_reason", "authority_status", "authority_block_reason", "risk_allowed", "risk_reason",
    "execution_mode", "execution_status", "current_stage", "completed_stages", "pending_stage",
    "final_block_reason", "created_at", "updated_at", "next_evaluation_at", "stale",
}

SYMBOL = "BTCUSDT"


def make_client():
    app = FastAPI()
    app.include_router(pipeline_module.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    modes.set_mode(modes.MODE_PAPER)
    db = SessionLocal()
    db.query(ExecutionIntentAudit).delete()
    db.query(Trade).delete()
    db.query(ActiveDriveDecisionConsumption).delete()
    db.query(ActiveDriveDecision).filter(ActiveDriveDecision.user_id == settings.admin_username).delete()
    row = db.get(UserBotSetting, settings.admin_username) or UserBotSetting(user_id=settings.admin_username)
    row.trading_profile = "short_term"
    db.add(row)
    db.commit()
    db.close()
    yield


def _make_approved_decision(**overrides) -> ActiveDriveDecision:
    execution_tf = resolve_execution_timeframe("short_term")
    defaults = dict(
        decision_id=f"pipeline-api-test-{datetime.now(timezone.utc).timestamp()}", user_id=settings.admin_username,
        engine="active_drive_v2", engine_version="2.2.0", symbol=SYMBOL, timeframe=execution_tf, signal="LONG",
        confidence=0.8, expected_edge=0.02, edge_supported=True, eligible_for_execution=True, blocking_reasons=[],
        decision_payload={"recommended_stop": 100.0, "recommended_target": 120.0, "required_confidence": 0.7,
                         "reference_price": 110.0},
        shadow=False,
    )
    defaults.update(overrides)
    db = SessionLocal()
    row = ActiveDriveDecision(**defaults)
    db.add(row)
    db.commit()
    finalized = finalize_decision_for_execution(db, row.decision_id)
    db.refresh(finalized)
    for column in ActiveDriveDecision.__table__.columns:
        getattr(finalized, column.name)
    db.expunge(finalized)
    db.close()
    return finalized


def test_response_has_every_required_field():
    """No decision exists yet, so timeframe resolution falls back to the
    user's configured (static) trading profile - every field the unified
    API contract promises must still be present."""
    client = make_client()
    r = client.get("/api/trading/pipeline/current", params={"symbol": SYMBOL})
    assert r.status_code == 200
    body = r.json()
    assert REQUIRED_FIELDS <= set(body.keys())
    assert body["timeframe_source"] == "configured_profile"
    assert body["authority_id"] is None
    assert body["execution_request_id"] is None
    assert body["order_id"] is None


def test_fully_approved_decision_resolves_configured_profile_timeframe():
    decision = _make_approved_decision()
    client = make_client()
    r = client.get("/api/trading/pipeline/current", params={"symbol": SYMBOL})
    assert r.status_code == 200
    body = r.json()
    assert body["timeframe"] == decision.timeframe
    assert body["timeframe_source"] == "configured_profile"
    assert body["authority_id"] == decision.decision_id
    assert body["execution_status"] == "approved_for_paper_execution"
    assert body["candidate_direction"] == decision.signal
    assert body["actionable"] is True


def test_repeated_reads_of_the_same_decision_are_identical_no_duplication():
    decision = _make_approved_decision()
    client = make_client()
    first = client.get("/api/trading/pipeline/current", params={"symbol": SYMBOL}).json()
    second = client.get("/api/trading/pipeline/current", params={"symbol": SYMBOL}).json()
    assert first["authority_id"] == second["authority_id"] == decision.decision_id
    assert first["execution_status"] == second["execution_status"]
    db = SessionLocal()
    assert db.query(ActiveDriveDecision).filter_by(decision_id=decision.decision_id).count() == 1
    db.close()
