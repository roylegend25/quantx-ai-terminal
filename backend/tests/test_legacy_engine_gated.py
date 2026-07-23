"""Active Drive V1 / the legacy ensemble engine has been removed from
Premium X Dark entirely - it now lives only in the standalone QuantX
Classic repository. It can never be selected, listed as available, or
influence the automated Active Drive V2 decision/execution path."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.bot as bot_module
from app.core.security import create_internal_service_token
from app.decision_engine.repository import is_available
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType


def make_client():
    app = FastAPI()
    app.include_router(bot_module.router)
    return TestClient(app)


def auth_headers():
    return {"Authorization": f"Bearer {create_internal_service_token()}"}


def test_active_drive_v1_has_no_registered_engine_and_reports_unavailable():
    assert DecisionEngineType.ACTIVE_DRIVE_V1 not in decision_engine_router.engines
    assert is_available(DecisionEngineType.ACTIVE_DRIVE_V1) is False
    assert is_available(DecisionEngineType.ACTIVE_DRIVE_V2) is True


def test_patch_decision_engine_rejects_v1_selection():
    client = make_client()
    r = client.patch("/api/bot/decision-engine", json={"engine": "active_drive_v1", "acknowledged": True},
                     headers=auth_headers())
    assert r.status_code == 409


def test_get_decision_engine_never_lists_v1_and_reports_v2_available():
    client = make_client()
    r = client.get("/api/bot/decision-engine", headers=auth_headers())
    assert r.status_code == 200
    engines = {e["id"]: e for e in r.json()["available_engines"]}
    assert "active_drive_v1" not in engines
    assert engines["active_drive_v2"]["available"] is True
