"""Active Drive V1 / the legacy ensemble engine (archived at
archive/quantx-classic) must be disabled in production and can never
influence the automated Active Drive V2 decision/execution path, even if
somehow re-selected."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.bot as bot_module
from app.core.config import settings
from app.core.security import create_internal_service_token
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
