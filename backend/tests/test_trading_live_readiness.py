"""Binance Live tab backing endpoints: live-readiness checklist, aggregated
risk-status, and the labeled execution-log feed (see
app/api/trading_control.py). All three are read-only/non-admin, assembled
from modules that already guarantee no secrets leak - this file re-asserts
that guarantee plus the checklist's per-condition blocked_reason."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.exchange as exchange_module
import app.api.portfolio as portfolio_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingAuditLog, TradingControl
from app.trading import modes, real_risk_gate
from tests.test_portfolio_api import MockReadClient, configure_keys, use_mock_read_client

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(TradingAuditLog).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    monkeypatch.setattr(exchange_module, "_connected_cache", (0.0, False))
    real_risk_gate.reset_duplicate_guard()
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def mock_connected(monkeypatch, value: bool):
    async def fake(*_a, **_kw):
        return value

    monkeypatch.setattr(exchange_module, "_binance_connected", fake)


def go_fully_ready(monkeypatch):
    """Every live-readiness gate passing: configured, connected, server
    lock on, live credentials configured, unlocked, an active lease, mode
    LIVE, kill switch off, valid limits, balance."""
    use_mock_read_client(monkeypatch)
    mock_connected(monkeypatch, True)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_SECRET)
    monkeypatch.setattr(settings, "binance_max_leverage", 3)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 10)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 5)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user=settings.admin_username)


# --------------------------------------------------------- live-readiness

def test_live_readiness_all_pass(monkeypatch):
    go_fully_ready(monkeypatch)
    client = make_client()
    r = client.get("/api/trading/live-readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["blocked_reason"] is None
    assert len(body["steps"]) == 10
    assert all(s["passed"] for s in body["steps"])
    keys = [s["key"] for s in body["steps"]]
    assert keys == [
        "binance_api_configured", "binance_account_connected", "server_live_lock",
        "user_live_unlock", "active_mode_binance_live", "live_credentials_configured",
        "live_authorization_lease", "kill_switch_off", "risk_limits_valid", "balance_available",
    ]


def test_live_readiness_blocked_by_server_lock(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    client = make_client()
    body = client.get("/api/trading/live-readiness").json()
    assert body["ok"] is False
    assert body["blocked_reason"] == "Server live lock enabled"


def test_live_readiness_blocked_by_user_unlock(monkeypatch):
    use_mock_read_client(monkeypatch)
    mock_connected(monkeypatch, True)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_max_leverage", 3)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 10)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 5)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT"])
    modes.set_mode(modes.MODE_LIVE)  # no unlock_live() call
    client = make_client()
    body = client.get("/api/trading/live-readiness").json()
    assert body["ok"] is False
    assert body["blocked_reason"] == "User live confirmation completed"


def test_live_readiness_blocked_by_mode(monkeypatch):
    # Leaving MODE_LIVE (even transiently, e.g. back to PAPER) always
    # re-arms live_unlocked=False by design (modes.set_mode) - so
    # "active_mode_binance_live" can never be the *sole* failing gate, it
    # always fails alongside "user_live_unlock". Assert the step itself
    # correctly reports failure and the current mode, and that
    # "user_live_unlock" (checked first) is what surfaces as blocked_reason.
    use_mock_read_client(monkeypatch)
    mock_connected(monkeypatch, True)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_max_leverage", 3)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 10)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 5)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT"])
    # stays in PAPER
    client = make_client()
    body = client.get("/api/trading/live-readiness").json()
    assert body["ok"] is False
    assert body["blocked_reason"] == "User live confirmation completed"
    mode_step = next(s for s in body["steps"] if s["key"] == "active_mode_binance_live")
    assert mode_step["passed"] is False
    assert mode_step["detail"] == modes.MODE_PAPER


def test_live_readiness_blocked_by_kill_switch(monkeypatch):
    go_fully_ready(monkeypatch)
    modes.set_kill_switch(True, reason="test")
    client = make_client()
    body = client.get("/api/trading/live-readiness").json()
    assert body["ok"] is False
    # Phase 31: Emergency Stop immediately revokes any active lease, so with
    # the lease check ordered before kill_switch_off, the lease is what
    # surfaces as blocked first - both are false, which is the point.
    assert body["blocked_reason"] == "Live-authorization lease active"
    kill_step = next(s for s in body["steps"] if s["key"] == "kill_switch_off")
    lease_step = next(s for s in body["steps"] if s["key"] == "live_authorization_lease")
    assert kill_step["passed"] is False
    assert lease_step["passed"] is False


def test_live_readiness_blocked_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client()
    body = client.get("/api/trading/live-readiness").json()
    assert body["ok"] is False
    assert body["blocked_reason"] == "Binance API configured"
    assert body["steps"][1]["passed"] is False  # connected also fails downstream


# ------------------------------------------------------------ risk-status

def test_risk_status_uses_binance_summary(monkeypatch):
    go_fully_ready(monkeypatch)
    client = make_client()
    r = client.get("/api/trading/binance/risk-status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["wallet_balance"] == 500.0
    assert body["available_balance"] == 400.0
    assert body["open_positions"] == 1
    assert body["open_orders"] == 1
    assert body["daily_pnl"] == -3.5
    assert body["daily_loss_used_usdt"] == 3.5
    assert body["active_mode"] == modes.MODE_LIVE


def test_risk_status_unavailable_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client()
    body = client.get("/api/trading/binance/risk-status").json()
    assert body["available"] is False
    assert body["open_orders"] is None


# --------------------------------------------------------- execution-log

def _seed_execution_audit():
    modes.audit("order_requested", symbol="BTCUSDT", detail={"side": "LONG", "notional": 10})
    modes.audit("order_blocked_by_risk_gate", symbol="BTCUSDT", detail={"check": "max_notional", "reason": "too big"})
    modes.audit("order_accepted", symbol="ETHUSDT", detail={"order_id": 1})
    modes.audit("order_filled", symbol="ETHUSDT", detail={"order_id": 1, "status": "FILLED"})
    modes.audit("trading_mode_changed", detail={"mode": "PAPER"})  # not an execution-log event


def test_execution_log_labels_stages_and_last_by_stage():
    _seed_execution_audit()
    client = make_client()
    r = client.get("/api/trading/binance/execution-log")
    assert r.status_code == 200
    body = r.json()
    events = body["events"]
    # the non-execution event (trading_mode_changed) must be filtered out
    assert all(e["event"] != "trading_mode_changed" for e in events)
    stages = {e["event"]: e["stage"] for e in events}
    assert stages["order_requested"] == "intent"
    assert stages["order_blocked_by_risk_gate"] == "risk_gate"
    assert stages["order_accepted"] == "router"
    assert stages["order_filled"] == "exchange"

    last = body["last_by_stage"]
    assert last["risk_gate"]["event"] == "order_blocked_by_risk_gate"
    assert last["risk_gate"]["detail"]["reason"] == "too big"
    assert last["exchange"]["event"] == "order_filled"


# ------------------------------------------------------------- no secrets

def test_binance_live_tab_endpoints_never_leak_secrets(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    _seed_execution_audit()
    client = make_client()

    for path in (
        "/api/trading/live-readiness",
        "/api/trading/binance/risk-status",
        "/api/trading/binance/execution-log",
    ):
        r = client.get(path)
        assert r.status_code == 200
        assert FAKE_KEY not in r.text
        assert FAKE_SECRET not in r.text
