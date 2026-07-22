"""Active Drive V1 / the legacy ensemble engine (archived at
archive/quantx-classic) must be disabled in production and can never
influence the automated Active Drive V2 decision/execution path, even if
somehow re-selected."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.bot as bot_module
from app.api.timeframes import can_persist_horizon_authority
from app.core.config import settings
from app.core.security import create_internal_service_token
from app.db.session import SessionLocal
from app.db.models import UserBotSetting
from app.decision_engine.repository import is_available
from app.decision_engine.types import DecisionEngineType


def make_client():
    app = FastAPI()
    app.include_router(bot_module.router)
    return TestClient(app)


def auth_headers():
    return {"Authorization": f"Bearer {create_internal_service_token()}"}


def test_active_drive_v1_reports_unavailable_in_production_config():
    assert settings.active_drive_v1_available is False
    assert is_available(DecisionEngineType.ACTIVE_DRIVE_V1) is False
    assert is_available(DecisionEngineType.ACTIVE_DRIVE_V2) is True


def test_patch_decision_engine_rejects_v1_selection():
    client = make_client()
    r = client.patch("/api/bot/decision-engine", json={"engine": "active_drive_v1", "acknowledged": True},
                     headers=auth_headers())
    assert r.status_code == 409


def test_get_decision_engine_reports_v1_as_unavailable():
    client = make_client()
    r = client.get("/api/bot/decision-engine", headers=auth_headers())
    assert r.status_code == 200
    engines = {e["id"]: e for e in r.json()["available_engines"]}
    assert engines["active_drive_v1"]["available"] is False
    assert engines["active_drive_v2"]["available"] is True


def test_horizon_authority_never_persistable_for_v1_even_if_forcibly_selected(monkeypatch):
    """Defense in depth: even bypassing the API and writing active_drive_v1
    directly into UserBotSetting, the automated Trading Horizon authority
    path still refuses to treat it as authoritative."""
    db = SessionLocal()
    try:
        row = db.get(UserBotSetting, "legacy-gate-test-user") or UserBotSetting(user_id="legacy-gate-test-user")
        row.decision_engine = "active_drive_v1"
        db.add(row)
        db.commit()
    finally:
        db.close()

    ok, reason = can_persist_horizon_authority(
        "active_drive_v1",
        {"required_timeframes": ["5m"], "structural_bias_timeframe": "4h", "symbol": "BTCUSDT"},
        {"5m": {"decision_id": "x", "engine": "active_drive_v1", "engine_version": "1.0.0",
                "symbol": "BTCUSDT", "timeframe": "5m"}},
    )
    assert ok is False
    assert reason == "ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE"
