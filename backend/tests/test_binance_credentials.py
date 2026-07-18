"""Phase 32: admin-only Binance Real API credential storage. Covers secret
handling (never returned/logged), admin-only access, password re-auth,
rate limiting, and that saving/testing/deleting a credential never touches
live-trading mode, the authorization lease, or the scheduler."""

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.binance_credentials as bc
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.db.models import BinanceCredential, TradingControl, LiveAuthorizationLease
from app.trading import modes

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt()).decode())
    monkeypatch.setattr(settings, "credential_encryption_key", __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "app_env", "development")  # skip the HTTPS-only check in tests
    db = SessionLocal()
    try:
        db.query(BinanceCredential).delete()
        db.query(TradingControl).delete()
        db.query(LiveAuthorizationLease).delete()
        db.commit()
    finally:
        db.close()
    bc._rate_state.clear()
    yield


def make_client():
    app = FastAPI()
    app.include_router(bc.router)
    return TestClient(app)


def admin_headers():
    return {"Authorization": f"Bearer {create_access_token(settings.admin_username)}"}


def other_user_headers():
    return {"Authorization": f"Bearer {create_access_token('someone-else')}"}


def save_body(**overrides):
    body = {"password": TEST_PASSWORD, "api_key": FAKE_KEY, "api_secret": FAKE_SECRET, "label": "main", "environment": "live"}
    body.update(overrides)
    return body


# --------------------------------------------------------------- 1: never returned

def test_secret_is_never_returned_after_saving():
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    body = r.json()
    assert "encrypted_api_key" not in body["status"]
    assert "encrypted_api_secret" not in body["status"]
    assert body["status"]["api_key_fingerprint"] == "AKIA" + "*" * (len(FAKE_KEY) - 8) + "7890"

    status = client.get("/api/admin/binance-credentials", headers=admin_headers()).json()
    assert FAKE_KEY not in client.get("/api/admin/binance-credentials", headers=admin_headers()).text
    assert FAKE_SECRET not in client.get("/api/admin/binance-credentials", headers=admin_headers()).text
    assert status["configured"] is True


def test_ciphertext_never_returned_and_password_never_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(bc, "log_event", lambda logger, **kw: logged.append(kw))
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert r.status_code == 200
    for entry in logged:
        serialized = str(entry)
        assert FAKE_KEY not in serialized
        assert FAKE_SECRET not in serialized
        assert TEST_PASSWORD not in serialized

    db = SessionLocal()
    try:
        row = db.get(BinanceCredential, 1)
        assert FAKE_KEY not in row.encrypted_api_key
        assert FAKE_SECRET not in row.encrypted_api_secret
    finally:
        db.close()


# ----------------------------------------------------------------- 3: admin only

def test_non_admin_cannot_save_credentials():
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=other_user_headers())
    assert r.status_code == 403
    db = SessionLocal()
    try:
        assert db.get(BinanceCredential, 1) is None
    finally:
        db.close()


def test_unauthenticated_request_is_rejected():
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body())
    assert r.status_code == 401


def test_wrong_password_is_rejected():
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(password="not the password"), headers=admin_headers())
    assert r.status_code == 401
    db = SessionLocal()
    try:
        assert db.get(BinanceCredential, 1) is None
    finally:
        db.close()


# --------------------------------------------------------------- 4/8: invalid creds fail safely, read-only test

def test_invalid_credentials_fail_safely(monkeypatch):
    async def fake_signed_get(base, path, api_key, api_secret):
        import httpx
        raise httpx.HTTPStatusError("bad", request=None, response=httpx.Response(401, request=httpx.Request("GET", "http://x")))

    monkeypatch.setattr(bc, "_signed_get", fake_signed_get)
    client = make_client()
    r = client.post("/api/admin/binance-credentials/test", json={"api_key": "bad", "api_secret": "bad"}, headers=admin_headers())
    assert r.status_code == 200  # the endpoint itself succeeds; the test result reports failure
    body = r.json()
    assert body["ok"] is False
    assert body["signed_read_ok"] is False
    assert "bad" not in body["detail"]  # no raw exception text leaked


def test_connection_test_is_read_only_and_never_places_an_order(monkeypatch):
    calls = []

    async def fake_signed_get(base, path, api_key, api_secret):
        calls.append(path)
        if "apiRestrictions" in path:
            return {"enableFutures": True, "enableWithdrawals": False}
        return {"totalWalletBalance": "1000"}

    monkeypatch.setattr(bc, "_signed_get", fake_signed_get)
    client = make_client()
    r = client.post("/api/admin/binance-credentials/test", json={"api_key": FAKE_KEY, "api_secret": FAKE_SECRET}, headers=admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["write_permission_detected"] is True
    assert body["withdraw_enabled_detected"] is False
    # only GET-style signed reads were ever issued - no order/cancel path exists on this module at all
    assert all("order" not in c.lower() for c in calls)
    import app.api.binance_credentials as mod
    assert not hasattr(mod, "place_order")
    assert not hasattr(mod, "submit_order")


# --------------------------------------------------------- 5/6: never enables live trading, restart stays paper

def test_saving_credentials_does_not_enable_live_trading(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert r.status_code == 200
    assert "execution remains PAPER" in r.json()["message"]
    assert modes.effective_mode() == modes.MODE_PAPER
    assert modes.get_control()["live_unlocked"] is False
    assert modes.get_active_lease() is None


def test_restart_after_saving_credentials_remains_paper_mode(monkeypatch):
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    # A stored credential must not itself survive as an active lease/unlock
    # across a restart - startup_safety_reset (Phase 31) still applies
    # regardless of whether a credential is on file.
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user=settings.admin_username)
    modes.startup_safety_reset()
    assert modes.effective_mode() == modes.MODE_PAPER
    assert modes.get_active_lease() is None
    # the credential itself is untouched by a restart
    db = SessionLocal()
    try:
        assert db.get(BinanceCredential, 1) is not None
    finally:
        db.close()


# ----------------------------------------------------------------------- 7: deletion

def test_credential_deletion_works():
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert client.get("/api/admin/binance-credentials", headers=admin_headers()).json()["configured"] is True

    r = client.request("DELETE", "/api/admin/binance-credentials", json={"password": TEST_PASSWORD}, headers=admin_headers())
    assert r.status_code == 200
    assert client.get("/api/admin/binance-credentials", headers=admin_headers()).json()["configured"] is False


def test_delete_requires_correct_password():
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    r = client.request("DELETE", "/api/admin/binance-credentials", json={"password": "wrong"}, headers=admin_headers())
    assert r.status_code == 401
    assert client.get("/api/admin/binance-credentials", headers=admin_headers()).json()["configured"] is True


# ----------------------------------------------------------------- credential resolution

def test_live_client_resolves_saved_db_credential(monkeypatch):
    """The saved, encrypted credential becomes what a write-capable
    BinanceFuturesClient actually loads - proving the storage path is wired
    end-to-end, not just persisted and ignored."""
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", "")
    monkeypatch.setattr(settings, "binance_live_api_secret", "")
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())

    live_client = BinanceFuturesClient(testnet=False, read_only=False)
    assert live_client._api_key == FAKE_KEY
    assert live_client._api_secret == FAKE_SECRET

    # a read-only client still uses the separate monitoring pair, never the
    # saved live credential
    read_client = BinanceFuturesClient(testnet=False, read_only=True)
    assert read_client._api_key != FAKE_KEY


def test_env_live_credential_takes_precedence_over_saved_db_credential(monkeypatch):
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", "env-key")
    monkeypatch.setattr(settings, "binance_live_api_secret", "env-secret")
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())

    live_client = BinanceFuturesClient(testnet=False, read_only=False)
    assert live_client._api_key == "env-key"


def test_binance_live_configured_recognizes_saved_credential(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_api_key", "")
    monkeypatch.setattr(settings, "binance_live_api_secret", "")
    assert modes.binance_live_configured() is False
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert modes.binance_live_configured() is True


# --------------------------------------------------------------------- rate limiting

def test_save_endpoint_is_rate_limited():
    client = make_client()
    for _ in range(bc._RATE_LIMITS["save"]):
        client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert r.status_code == 429


# ------------------------------------------------------------- encryption store gating

def test_save_refuses_when_encryption_key_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", "")
    client = make_client()
    r = client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    assert r.status_code == 503
    db = SessionLocal()
    try:
        assert db.get(BinanceCredential, 1) is None
    finally:
        db.close()


def test_status_endpoint_never_leaks_secrets_even_when_configured():
    client = make_client()
    client.post("/api/admin/binance-credentials", json=save_body(), headers=admin_headers())
    r = client.get("/api/admin/binance-credentials", headers=admin_headers())
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    assert TEST_PASSWORD not in r.text
