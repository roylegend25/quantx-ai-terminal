"""Phase 22 safety net: trading modes, live lock, kill switch, and the
guarantee that no API secret ever leaves the backend."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingControl
from app.exchanges.binance_futures_client import BinanceFuturesClient, LiveTradingLocked
from app.trading import modes, real_risk_gate
from app.trading.execution_router import ExecutionRouter


FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_control():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    yield


def make_trading_client():
    import app.api.trading_control as tc
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


# ------------------------------------------------------------ mode defaults

def test_default_mode_is_paper():
    assert modes.effective_mode() == modes.MODE_PAPER


def test_live_is_locked_by_default(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    allowed, reason = modes.can_trade()
    assert not allowed
    assert "disabled by server configuration" in reason


def test_live_requires_env_lock_and_unlock(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.set_mode(modes.MODE_LIVE)
    # env open but unlock ceremony not done -> still locked
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    modes.unlock_live()
    assert modes.effective_mode() == modes.MODE_LIVE
    # leaving live re-arms the lock
    modes.set_mode(modes.MODE_PAPER)
    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED


def test_production_client_refuses_to_construct_while_env_locked(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    with pytest.raises(LiveTradingLocked):
        BinanceFuturesClient(testnet=False)


# --------------------------------------------------------- secret exposure

def test_safe_status_never_contains_api_secrets(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)

    status = modes.exchange_safe_status()
    serialized = json.dumps(status)
    assert FAKE_KEY not in serialized
    assert FAKE_SECRET not in serialized
    assert status["binance_configured"] is True
    # required safe fields per spec
    for field in ("mode", "binance_configured", "testnet", "live_enabled", "can_trade", "reason"):
        assert field in status


def test_trading_mode_endpoint_never_leaks_secrets(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    client = make_trading_client()
    r = client.get("/api/trading/mode")
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


# ------------------------------------------------------------- live unlock

def test_live_unlock_refused_when_server_disabled(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    client = make_trading_client()
    r = client.post(
        "/api/trading/request-live-unlock",
        json={
            "confirmation": modes.LIVE_UNLOCK_PHRASE,
            "acknowledgements": {k: True for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS},
        },
    )
    assert r.status_code == 403
    assert "disabled by server configuration" in r.json()["detail"]
    assert modes.effective_mode() != modes.MODE_LIVE


def test_live_unlock_requires_exact_phrase_and_all_acknowledgements(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    client = make_trading_client()

    wrong_phrase = client.post(
        "/api/trading/request-live-unlock",
        json={
            "confirmation": "i understand live trading risk",
            "acknowledgements": {k: True for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS},
        },
    )
    assert wrong_phrase.status_code == 400

    missing_ack = client.post(
        "/api/trading/request-live-unlock",
        json={
            "confirmation": modes.LIVE_UNLOCK_PHRASE,
            "acknowledgements": {k: True for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS[:-1]},
        },
    )
    assert missing_ack.status_code == 400
    assert modes.effective_mode() != modes.MODE_LIVE

    complete = client.post(
        "/api/trading/request-live-unlock",
        json={
            "confirmation": modes.LIVE_UNLOCK_PHRASE,
            "acknowledgements": {k: True for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS},
        },
    )
    assert complete.status_code == 200
    assert modes.effective_mode() == modes.MODE_LIVE


def test_no_live_order_possible_while_env_disabled(monkeypatch):
    """Even a maliciously unlocked control row cannot trade live while
    BINANCE_LIVE_ENABLED=false - the mode degrades to LOCKED and the router
    refuses before any provider is constructed."""
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED

    router = ExecutionRouter()
    import asyncio
    result = asyncio.run(
        router.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=10)
    )
    assert not result.ok
    assert "locked" in result.reason.lower()


# -------------------------------------------------------------- kill switch

def test_kill_switch_blocks_router_and_paper_risk_gate():
    from app.trading import risk_manager

    modes.set_kill_switch(True, reason="test")
    try:
        router = ExecutionRouter()
        import asyncio
        result = asyncio.run(
            router.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=10)
        )
        assert not result.ok
        assert "kill switch" in result.reason.lower()

        decision = risk_manager.evaluate_risk(confidence=99.0, direction="LONG", open_positions=0)
        assert decision["allowed"] is False
        assert "kill switch" in decision["reason"].lower()
    finally:
        modes.set_kill_switch(False)

    decision = risk_manager.evaluate_risk(confidence=99.0, direction="LONG", open_positions=0)
    assert "kill switch" not in decision["reason"].lower()


def test_kill_switch_endpoint_stops_bot_and_blocks_trades():
    from app.api.bot import BOT_STATE

    client = make_trading_client()
    r = client.post("/api/trading/kill-switch", json={"active": True, "reason": "emergency"})
    assert r.status_code == 200
    assert BOT_STATE["status"] == "stopped"
    assert modes.kill_switch_active() is True

    order = client.post(
        "/api/trading/place-order",
        json={"symbol": "BTCUSDT", "side": "LONG", "notional_usdt": 10},
    )
    assert order.status_code == 400
    assert "kill switch" in order.json()["detail"].lower()

    off = client.post("/api/trading/kill-switch", json={"active": False})
    assert off.status_code == 200
    assert modes.kill_switch_active() is False
