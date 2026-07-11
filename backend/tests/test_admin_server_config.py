"""Phase 24: allowlist-only .env manager + admin-only server-config API.

Every test operates on a temp .env via ENV_FILE_PATH - the real deployment
file is never touched. FAKE secret values planted in the file/settings are
asserted to never leak through any response."""

import json
import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.admin_config as admin_module
from app.core import env_manager
from app.core.config import settings
from app.core.security import create_access_token, create_internal_service_token
from app.db.session import SessionLocal
from app.db.models import TradingAuditLog, TradingControl
from app.trading import modes

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"

ENV_CONTENT = f"""# QuantX deployment config
SECRET_KEY=jwt-{FAKE_SECRET}
BINANCE_API_KEY={FAKE_KEY}
BINANCE_API_SECRET={FAKE_SECRET}

# trading limits
BINANCE_LIVE_ENABLED=false
BINANCE_MAX_LEVERAGE=3
BINANCE_ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT
"""


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(ENV_CONTENT)
    monkeypatch.setenv("ENV_FILE_PATH", str(path))
    yield str(path)


@pytest.fixture(autouse=True)
def restore_settings(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    monkeypatch.setattr(settings, "binance_max_leverage", 3.0)
    monkeypatch.setattr(settings, "binance_default_leverage", 1.0)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 25.0)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 20.0)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(TradingAuditLog).delete()
        db.commit()
    finally:
        db.close()
    yield


def make_client():
    app = FastAPI()
    app.include_router(admin_module.router)
    return TestClient(app)


def admin_headers():
    return {"Authorization": f"Bearer {create_access_token(settings.admin_username)}"}


def non_admin_headers():
    return {"Authorization": f"Bearer {create_internal_service_token()}"}


def full_enable_body(enabled=True):
    return {
        "enabled": enabled,
        "typed_confirmation": modes.LIVE_UNLOCK_PHRASE,
        "acknowledgements": {k: True for k in admin_module.SERVER_LOCK_ACKNOWLEDGEMENTS},
    }


# ------------------------------------------------------------- env manager

def test_unknown_and_forbidden_keys_rejected(env_file):
    with pytest.raises(env_manager.EnvUpdateError):
        env_manager.validate_updates({"TOTALLY_UNKNOWN": "x"})
    for forbidden in env_manager.FORBIDDEN_ENV_KEYS:
        with pytest.raises(env_manager.EnvUpdateError):
            env_manager.validate_updates({forbidden: "x"})
        assert forbidden not in env_manager.ALLOWED_ENV_KEYS


def test_update_preserves_comments_secrets_and_creates_backup(env_file):
    changes = env_manager.update_env_file({"BINANCE_LIVE_ENABLED": True}, path=env_file)
    assert changes == {"BINANCE_LIVE_ENABLED": {"old": "false", "new": "true"}}

    content = open(env_file).read()
    assert "BINANCE_LIVE_ENABLED=true" in content
    assert "# QuantX deployment config" in content  # comments preserved
    assert f"BINANCE_API_SECRET={FAKE_SECRET}" in content  # untouched secret line

    backups = [f for f in os.listdir(os.path.dirname(env_file)) if f.startswith(".env.backup.")]
    assert len(backups) == 1
    backup_content = open(os.path.join(os.path.dirname(env_file), backups[0])).read()
    assert "BINANCE_LIVE_ENABLED=false" in backup_content  # pre-change state secured

    for f in [env_file, os.path.join(os.path.dirname(env_file), backups[0])]:
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_backup_honors_env_backup_dir(env_file, tmp_path, monkeypatch):
    backup_dir = tmp_path / "persist"
    monkeypatch.setenv("ENV_BACKUP_DIR", str(backup_dir))
    env_manager.update_env_file({"BINANCE_MAX_LEVERAGE": 2}, path=env_file)
    backups = list(backup_dir.glob(".env.backup.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(os.stat(backups[0]).st_mode) == 0o600


def test_update_appends_missing_key_and_creates_file(tmp_path):
    path = str(tmp_path / "fresh.env")
    env_manager.update_env_file({"BINANCE_MAX_NOTIONAL_PER_TRADE": 10}, path=path)
    assert open(path).read().strip() == "BINANCE_MAX_NOTIONAL_PER_TRADE=10"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_read_env_values_only_returns_allowed_keys(env_file):
    values = env_manager.read_env_values(env_file)
    assert set(values) <= env_manager.ALLOWED_ENV_KEYS
    assert FAKE_SECRET not in json.dumps(values)


def test_apply_to_settings_makes_file_authoritative(env_file, monkeypatch):
    env_manager.update_env_file(
        {"BINANCE_LIVE_ENABLED": True, "BINANCE_MAX_NOTIONAL_PER_TRADE": 12.5,
         "BINANCE_ALLOWED_SYMBOLS": "BTCUSDT"},
        path=env_file,
    )
    applied = env_manager.apply_to_settings(env_file)
    assert settings.binance_live_enabled is True
    assert settings.binance_max_notional_per_trade == 12.5
    assert settings.binance_allowed_symbols == ["BTCUSDT"]
    assert "binance_api_key" not in applied  # secrets never pass through


def test_symbol_validation(env_file):
    with pytest.raises(env_manager.EnvUpdateError):
        env_manager.validate_updates({"BINANCE_ALLOWED_SYMBOLS": ""})
    with pytest.raises(env_manager.EnvUpdateError):
        env_manager.validate_updates({"BINANCE_ALLOWED_SYMBOLS": "BTCUSD"})  # not a USDT pair
    ok = env_manager.validate_updates({"BINANCE_ALLOWED_SYMBOLS": "btcusdt, ethusdt"})
    assert ok["BINANCE_ALLOWED_SYMBOLS"] == "BTCUSDT,ETHUSDT"


# ----------------------------------------------------------------- admin API

def test_non_admin_gets_403_everywhere_and_is_audited(env_file):
    client = make_client()
    for method, url, body in [
        ("GET", "/api/admin/server-config", None),
        ("POST", "/api/admin/server-config/binance-live", full_enable_body()),
        ("PATCH", "/api/admin/server-config/risk-limits", {"BINANCE_MAX_LEVERAGE": 2}),
        ("POST", "/api/admin/server-config/reload", {}),
    ]:
        r = client.request(method, url, json=body, headers=non_admin_headers())
        assert r.status_code == 403, url
        # and unauthenticated is 401
        r = client.request(method, url, json=body)
        assert r.status_code == 401, url

    db = SessionLocal()
    try:
        events = db.query(TradingAuditLog).filter(TradingAuditLog.event == "server_config_unauthorized_attempt").all()
        assert len(events) >= 4
        assert events[0].detail["subject"] == "internal-scheduler"
    finally:
        db.close()

    # .env untouched by any of it
    assert "BINANCE_LIVE_ENABLED=false" in open(env_file).read()


def test_admin_get_config_is_safe(env_file):
    client = make_client()
    r = client.get("/api/admin/server-config", headers=admin_headers())
    assert r.status_code == 200
    assert FAKE_KEY not in r.text and FAKE_SECRET not in r.text
    data = r.json()
    assert data["binance_api_key_configured"] is True
    assert data["binance_api_secret_configured"] is True
    assert data["binance_live_enabled"] is False
    assert data["restart_required"] is False


def test_enable_live_requires_phrase_and_all_acknowledgements(env_file):
    client = make_client()

    body = full_enable_body()
    body["typed_confirmation"] = "i understand"
    assert client.post("/api/admin/server-config/binance-live", json=body, headers=admin_headers()).status_code == 400

    body = full_enable_body()
    body["acknowledgements"].pop("withdrawals_disabled")
    assert client.post("/api/admin/server-config/binance-live", json=body, headers=admin_headers()).status_code == 400

    assert settings.binance_live_enabled is False
    assert "BINANCE_LIVE_ENABLED=false" in open(env_file).read()


def test_enable_then_disable_live_updates_env_and_settings(env_file):
    client = make_client()

    on = client.post("/api/admin/server-config/binance-live", json=full_enable_body(), headers=admin_headers())
    assert on.status_code == 200
    assert settings.binance_live_enabled is True
    assert "BINANCE_LIVE_ENABLED=true" in open(env_file).read()
    assert "user live-risk confirmation is still required" in on.json()["message"]

    # disabling needs no ceremony and locks immediately
    off = client.post("/api/admin/server-config/binance-live", json={"enabled": False}, headers=admin_headers())
    assert off.status_code == 200
    assert settings.binance_live_enabled is False
    assert "BINANCE_LIVE_ENABLED=false" in open(env_file).read()

    # even a user-unlocked live mode degrades to LOCKED once the server lock is off
    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    modes.set_mode(modes.MODE_PAPER)

    db = SessionLocal()
    try:
        events = [r.event for r in db.query(TradingAuditLog).all()]
        assert "server_live_lock_enabled" in events
        assert "server_live_lock_disabled" in events
        enabled_row = db.query(TradingAuditLog).filter(TradingAuditLog.event == "server_live_lock_enabled").first()
        assert enabled_row.detail["admin"] == settings.admin_username
        assert enabled_row.detail["changes"]["BINANCE_LIVE_ENABLED"] == {"old": "false", "new": "true"}
    finally:
        db.close()


def test_risk_limits_validation_and_update(env_file):
    client = make_client()
    h = admin_headers()

    assert client.patch("/api/admin/server-config/risk-limits", json={"BINANCE_MAX_LEVERAGE": 0.5}, headers=h).status_code == 400
    assert client.patch("/api/admin/server-config/risk-limits", json={"BINANCE_MAX_LEVERAGE": 5}, headers=h).status_code == 400  # >3 without flag
    assert client.patch("/api/admin/server-config/risk-limits", json={"BINANCE_MAX_LEVERAGE": 50, "allow_high_leverage": True}, headers=h).status_code == 400  # > hard cap
    assert client.patch("/api/admin/server-config/risk-limits", json={"BINANCE_MAX_NOTIONAL_PER_TRADE": -5}, headers=h).status_code == 400
    assert client.patch("/api/admin/server-config/risk-limits", json={"BINANCE_ALLOWED_SYMBOLS": ""}, headers=h).status_code == 400
    assert client.patch(
        "/api/admin/server-config/risk-limits",
        json={"BINANCE_DEFAULT_LEVERAGE": 3, "BINANCE_MAX_LEVERAGE": 2}, headers=h
    ).status_code == 400  # default > max

    ok = client.patch(
        "/api/admin/server-config/risk-limits",
        json={
            "BINANCE_DEFAULT_LEVERAGE": 1,
            "BINANCE_MAX_LEVERAGE": 2,
            "BINANCE_MAX_NOTIONAL_PER_TRADE": 10,
            "BINANCE_MAX_DAILY_LOSS_USDT": 5,
            "BINANCE_ALLOWED_SYMBOLS": "BTCUSDT",
        },
        headers=h,
    )
    assert ok.status_code == 200
    assert settings.binance_max_leverage == 2.0
    assert settings.binance_max_notional_per_trade == 10.0
    assert settings.binance_allowed_symbols == ["BTCUSDT"]
    content = open(env_file).read()
    assert "BINANCE_MAX_NOTIONAL_PER_TRADE=10" in content
    assert "BINANCE_ALLOWED_SYMBOLS=BTCUSDT" in content


def test_api_key_cannot_be_edited_via_any_surface(env_file):
    client = make_client()
    # unknown fields on the risk-limits body are ignored by pydantic and the
    # allowlist rejects them if smuggled through the raw manager
    r = client.patch(
        "/api/admin/server-config/risk-limits",
        json={"BINANCE_API_KEY": "evil", "BINANCE_MAX_LEVERAGE": 2},
        headers=admin_headers(),
    )
    assert r.status_code == 200
    content = open(env_file).read()
    assert f"BINANCE_API_KEY={FAKE_KEY}" in content  # unchanged
    assert "evil" not in content


def test_reload_endpoint(env_file):
    env_manager.update_env_file({"BINANCE_MAX_DAILY_LOSS_USDT": 7}, path=env_file)
    client = make_client()
    r = client.post("/api/admin/server-config/reload", headers=admin_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["restart_required"] is False
    assert settings.binance_max_daily_loss_usdt == 7.0
    assert FAKE_SECRET not in r.text
