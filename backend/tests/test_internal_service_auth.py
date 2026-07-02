from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.security import (
    INTERNAL_SERVICE_SUBJECT,
    create_internal_service_token,
    decode_access_token,
)
from app.engine.trading_engine import TradingEngine


def test_internal_service_token_decodes_to_expected_subject():
    token = create_internal_service_token()
    assert decode_access_token(token) == INTERNAL_SERVICE_SUBJECT


def test_internal_service_token_is_accepted_by_protected_routes():
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(get_current_user)])
    async def protected():
        return {"ok": True}

    client = TestClient(app)

    # matches the exact symptom that was reported: internal background loops
    # calling protected endpoints with no auth header got 401
    unauthenticated = client.get("/protected")
    assert unauthenticated.status_code == 401

    token = create_internal_service_token()
    authenticated = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert authenticated.status_code == 200


def test_trading_engine_generates_a_usable_token_on_init():
    engine = TradingEngine()
    assert decode_access_token(engine._token) == INTERNAL_SERVICE_SUBJECT
