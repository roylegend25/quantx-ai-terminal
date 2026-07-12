"""Binance Live tab's Risk Status and Bot Decision Status cards
(app.api.trading_control.binance_risk_status / binance_decision_status).

These two endpoints must never read or leak paper-ledger state, must
reflect the real trading-control locks (server lock, user unlock, kill
switch, active mode) and real Binance account data, must degrade safely
when the account can't be reached, and must never expose API keys/secrets."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_module
import app.api.prediction as prediction_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import BinanceBotTrade, Trade, TradingAuditLog, TradingControl
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes
from tests.test_portfolio_api import MockReadClient, configure_keys, open_paper_trade, use_mock_read_client

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(TradingAuditLog).delete()
        db.query(BinanceBotTrade).delete()
        db.query(Trade).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    snapshot_service.reset()
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def go_fully_ready(monkeypatch):
    use_mock_read_client(monkeypatch)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_max_leverage", 3)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 10)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 5)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()


def fake_prediction(direction="SHORT", confidence=62.4, required_confidence=70.0, computed_at=1750000000000):
    """Matches the real GET /api/prediction/{symbol} response shape: a
    top-level response wrapping the actual make_prediction() output under
    "prediction" - direction/confidence/target/stop/decision_engine are
    mirrored at both levels, but computed_at lives only in the nested dict
    (see app.api.prediction.prediction)."""

    async def fn(symbol, interval="5m", timeframe=None, limit=220):
        decision_engine = {
            "active_model": {"model_type": "lightgbm-v34", "name": "lightgbm-v34"},
            "model_votes": [
                {"name": "Champion ML", "direction": direction, "available": True, "drives_decision": True},
            ],
            "strategy_used": "strategy_ensemble",
            "required_confidence": required_confidence,
        }
        return {
            "direction": direction,
            "confidence": confidence,
            "target": 63100.0,
            "stop": 64600.0,
            "decision_engine": decision_engine,
            "prediction": {
                "direction": direction,
                "confidence": confidence,
                "decision_engine": decision_engine,
                "computed_at": computed_at,
            },
        }

    return fn


def fake_no_data_prediction():
    async def fn(symbol, interval="5m", timeframe=None, limit=220):
        return None

    return fn


# ------------------------------------------------------------ risk-status

def test_risk_status_reflects_lock_states(monkeypatch):
    go_fully_ready(monkeypatch)
    client = make_client()
    body = client.get("/api/trading/binance/risk-status").json()

    assert body["active_mode"] == modes.MODE_LIVE
    assert body["binance_live_enabled_by_server"] is True
    assert body["binance_live_unlocked_by_user"] is True
    assert body["kill_switch_active"] is False
    assert body["account_connected"] is True
    assert body["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert isinstance(body["risk_score"], float)
    assert body["current_effective_leverage"] == 2.0  # MockReadClient's ETHUSDT position leverage


def test_risk_status_kill_switch_reflected(monkeypatch):
    go_fully_ready(monkeypatch)
    modes.set_kill_switch(True, reason="test")
    client = make_client()
    body = client.get("/api/trading/binance/risk-status").json()

    assert body["kill_switch_active"] is True
    assert any("Kill switch" in r for r in body["blocked_reasons"])


def test_risk_status_does_not_use_paper_state(monkeypatch):
    """A paper trade's balance/pnl must have no effect on the Binance risk
    snapshot - the two ledgers are strictly separate."""
    open_paper_trade(pnl=999999.0)
    go_fully_ready(monkeypatch)
    client = make_client()
    body = client.get("/api/trading/binance/risk-status").json()

    assert body["wallet_balance"] == 500.0  # from MockReadClient, unaffected by the paper trade
    assert "paper_balance" not in body
    assert "paper_pnl" not in body


def test_risk_status_account_disconnected_handled_safely(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client()
    r = client.get("/api/trading/binance/risk-status")

    assert r.status_code == 200
    body = r.json()
    assert body["account_connected"] is False
    assert body["risk_level"] == "UNKNOWN"
    assert body["risk_score"] is None
    assert "Binance API keys are not configured" in body["blocked_reasons"]


def test_risk_status_no_secrets(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    client = make_client()
    r = client.get("/api/trading/binance/risk-status")

    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


# -------------------------------------------------------- decision-status

def test_decision_status_no_data_when_prediction_unavailable(monkeypatch):
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/decision-status").json()

    assert body == {"status": "NO_DATA", "message": "No Binance live decision has been evaluated yet."}


def test_decision_status_blocked_when_active_mode_is_paper(monkeypatch):
    use_mock_read_client(monkeypatch)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    # stays in PAPER (default)
    client = make_client()
    body = client.get("/api/trading/binance/decision-status").json()

    assert body["active_mode"] == modes.MODE_PAPER
    assert body["final_direction"] == "BLOCKED"
    assert body["risk_gate_allowed"] is False
    assert any("Active mode is PAPER" in r for r in body["blocked_reasons"])
    assert body["strategy_direction"] == "SHORT"
    assert body["model_direction"] == "SHORT"
    assert body["confidence"] == 62.4
    assert body["required_confidence"] == 70.0
    assert body["active_model"] == "lightgbm-v34"
    assert body["active_strategy"] == "strategy_ensemble"


def test_decision_status_allowed_when_fully_ready(monkeypatch):
    from app.risk import settings_repository as risk_settings_repo
    go_fully_ready(monkeypatch)
    # MockReadClient reports 1 open position - raise the cap so it isn't
    # itself a blocker for this "everything ready" case.
    real_get_settings = risk_settings_repo.get_settings
    monkeypatch.setattr(
        risk_settings_repo, "get_settings",
        lambda db=None: {**real_get_settings(db=db), "max_open_positions": 5},
    )
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction(confidence=90.0))
    client = make_client()
    body = client.get("/api/trading/binance/decision-status").json()

    assert body["active_mode"] == modes.MODE_LIVE
    assert body["risk_gate_allowed"] is True
    assert body["final_direction"] == "SHORT"
    assert body["blocked_reasons"] == []


def test_decision_status_does_not_use_paper_state(monkeypatch):
    open_paper_trade(pnl=999999.0)
    use_mock_read_client(monkeypatch)
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/decision-status").json()

    assert "paper_pnl" not in body
    assert "paper_balance" not in body
    assert "trade_id" not in body


def test_decision_status_kill_switch_reflected(monkeypatch):
    go_fully_ready(monkeypatch)
    modes.set_kill_switch(True, reason="test")
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/decision-status").json()

    assert body["final_direction"] == "BLOCKED"
    assert any("Kill switch" in r for r in body["blocked_reasons"])


def test_decision_status_no_secrets(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    client = make_client()
    r = client.get("/api/trading/binance/decision-status")

    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
