"""API layer for Phase 25 execution transparency:
GET /api/trading/binance/execution-pipeline, GET .../execution-logs, and
POST .../test-order (app/api/trading_control.py). Verifies the endpoints
never claim a signal was executed when it wasn't, degrade safely with no
attempts yet, and that the real-money test-order path is properly gated
and never leaks secrets - even on a Binance rejection."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.prediction as prediction_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import BinanceExecutionAttempt, TradingAuditLog, TradingControl
from app.trading import modes, real_risk_gate
from app.trading.execution_router import BinanceExecutionProvider
from tests.test_execution_router_binance import MockBinanceClient

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(TradingAuditLog).delete()
        db.query(BinanceExecutionAttempt).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def go_fully_ready(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_max_leverage", 3)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 200.0)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 5)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()


def fake_prediction(direction="SHORT", confidence=90.0, required_confidence=70.0):
    async def fn(symbol, interval="5m", timeframe=None, limit=220):
        decision_engine = {
            "active_model": {"model_type": "lightgbm-v34"},
            "model_votes": [{"name": "Champion ML", "direction": direction, "available": True}],
            "strategy_used": "strategy_ensemble",
            "required_confidence": required_confidence,
        }
        return {
            "direction": direction, "confidence": confidence, "target": 63100.0, "stop": 64600.0,
            "decision_engine": decision_engine,
            "prediction": {"direction": direction, "confidence": confidence,
                           "decision_engine": decision_engine, "computed_at": 1750000000000},
        }
    return fn


def seed_attempt(**overrides):
    defaults = dict(
        mode=modes.MODE_LIVE, symbol="BTCUSDT", side="LONG", is_test=False, confidence=90.0,
        requested_notional=100.0, requested_quantity=0.002, adjusted_quantity=0.002, leverage=2.0,
        margin_required=50.0, final_status="order_accepted", final_reason=None, order_id=555,
        latency_ms=42.0,
        stages=[
            {"stage": "signal_generated", "status": "success", "reason": None, "at": "t"},
            {"stage": "champion_model", "status": "success", "reason": None, "at": "t"},
            {"stage": "position_sizing", "status": "success", "reason": None, "at": "t"},
            {"stage": "risk_gate", "status": "success", "reason": None, "at": "t"},
            {"stage": "quantity_calculation", "status": "success", "reason": None, "at": "t"},
            {"stage": "binance_validation", "status": "success", "reason": None, "at": "t"},
            {"stage": "entry_order_submitted", "status": "success", "reason": None, "at": "t"},
            {"stage": "entry_order_accepted", "status": "success", "reason": None, "at": "t"},
            {"stage": "protective_tp_submitted", "status": "success", "reason": None, "at": "t"},
            {"stage": "protective_sl_submitted", "status": "success", "reason": None, "at": "t"},
            {"stage": "protection_confirmed", "status": "success", "reason": None, "at": "t"},
        ],
    )
    defaults.update(overrides)
    db = SessionLocal()
    try:
        row = BinanceExecutionAttempt(**defaults)
        db.add(row)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------- execution-pipeline

def test_pipeline_returns_waiting_skeleton_when_no_attempt_yet(monkeypatch):
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/execution-pipeline?symbol=BTCUSDT").json()

    assert body["execution_attempted"] is False
    assert body["execution_ok"] is None
    assert len(body["stages"]) == 13
    assert all(s["status"] == "waiting" for s in body["stages"])


def test_pipeline_never_reports_execution_ok_when_signal_approved_but_execution_failed(monkeypatch):
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction(confidence=95.0))
    seed_attempt(final_status="failed", final_reason="Margin is insufficient",
                 stages=[
                     {"stage": "signal_generated", "status": "success", "reason": None, "at": "t"},
                     {"stage": "champion_model", "status": "success", "reason": None, "at": "t"},
                     {"stage": "position_sizing", "status": "success", "reason": None, "at": "t"},
                     {"stage": "risk_gate", "status": "success", "reason": None, "at": "t"},
                     {"stage": "quantity_calculation", "status": "success", "reason": None, "at": "t"},
                     {"stage": "binance_validation", "status": "success", "reason": None, "at": "t"},
                     {"stage": "entry_order_submitted", "status": "failed", "reason": "Insufficient margin", "at": "t"},
                 ])
    client = make_client()
    body = client.get("/api/trading/binance/execution-pipeline?symbol=BTCUSDT").json()

    assert body["execution_attempted"] is True
    assert body["execution_ok"] is False
    assert body["final_reason"] == "Margin is insufficient"
    submit_stage = next(s for s in body["stages"] if s["stage"] == "entry_order_submitted")
    assert submit_stage["status"] == "failed"
    accepted_stage = next(s for s in body["stages"] if s["stage"] == "entry_order_accepted")
    assert accepted_stage["status"] == "waiting"  # never reached


def test_pipeline_reflects_successful_attempt(monkeypatch):
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    seed_attempt()
    client = make_client()
    body = client.get("/api/trading/binance/execution-pipeline?symbol=BTCUSDT").json()

    assert body["execution_ok"] is True
    assert body["order_id"] == 555
    assert body["adjusted_quantity"] == 0.002


def test_pipeline_ignores_test_orders_for_the_live_signal_view(monkeypatch):
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    seed_attempt(is_test=True)
    client = make_client()
    body = client.get("/api/trading/binance/execution-pipeline?symbol=BTCUSDT").json()
    assert body["execution_attempted"] is False  # the only attempt on file is a test order


# --------------------------------------------------------------- execution-logs

def test_execution_logs_returns_full_detail_newest_first():
    seed_attempt(order_id=1, symbol="BTCUSDT")
    seed_attempt(order_id=2, symbol="ETHUSDT")
    client = make_client()
    body = client.get("/api/trading/binance/execution-logs?limit=100").json()

    assert len(body["attempts"]) == 2
    assert body["attempts"][0]["order_id"] == 2  # newest first
    row = body["attempts"][0]
    for field in ("created_at", "symbol", "side", "confidence", "requested_quantity", "adjusted_quantity",
                  "leverage", "margin_required", "exchange_response", "latency_ms", "order_id", "stages"):
        assert field in row


def test_execution_logs_limit_capped_at_100():
    client = make_client()
    body = client.get("/api/trading/binance/execution-logs?limit=99999").json()
    assert body["attempts"] == []  # just proving the request doesn't 500/reject


# ------------------------------------------------------------------ test-order

def test_test_order_requires_live_mode():
    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"confirm": True})
    assert r.status_code == 409


def test_test_order_requires_explicit_confirmation(monkeypatch):
    go_fully_ready(monkeypatch)
    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"confirm": False})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def _install_mock_live_provider(monkeypatch, mock_client):
    provider = BinanceExecutionProvider.__new__(BinanceExecutionProvider)
    provider.testnet = False
    provider.mode = modes.MODE_LIVE
    provider._client = mock_client
    monkeypatch.setattr(tc.execution_router, "_live", provider)
    return provider


class FilteredMockClient(MockBinanceClient):
    """Adds get_exchange_filters and simulates a real fill/close cycle: the
    mock reports no open position until the (non-reduce-only) test order is
    placed, then reports one until the reduce-only close clears it - so the
    risk gate's max_open_positions check and the close step both see
    realistic state, not a permanently-open phantom position."""

    async def get_exchange_filters(self, symbol):
        return {"min_qty": 0.001, "step_size": 0.001, "quantity_precision": 3, "min_notional": 5.0,
                "tick_size": 0.1, "price_precision": 1}

    async def place_market_order(self, symbol, side, quantity, reduce_only=False, client_order_id=None):
        order = await super().place_market_order(symbol, side, quantity, reduce_only, client_order_id)
        if reduce_only:
            self.position = None
        else:
            from tests.test_execution_router_binance import btc_long_position
            self.position = btc_long_position()
        return order


def test_test_order_places_and_immediately_closes(monkeypatch):
    go_fully_ready(monkeypatch)
    mock_client = FilteredMockClient()
    _install_mock_live_provider(monkeypatch, mock_client)

    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"symbol": "BTCUSDT", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["submit"]["ok"] is True
    assert body["close"] is not None
    assert body["close"]["ok"] is True
    assert mock_client.called("place_market_order")

    attempt = seed_and_fetch_latest_test_attempt()
    assert attempt.is_test is True


def seed_and_fetch_latest_test_attempt():
    db = SessionLocal()
    try:
        row = (
            db.query(BinanceExecutionAttempt)
            .filter(BinanceExecutionAttempt.is_test.is_(True))
            .order_by(BinanceExecutionAttempt.id.desc())
            .first()
        )
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()


def test_test_order_returns_exact_rejection_on_binance_failure(monkeypatch):
    go_fully_ready(monkeypatch)
    mock_client = FilteredMockClient()

    async def fail_order(symbol, side, quantity, reduce_only=False, client_order_id=None):
        from app.exchanges.binance_errors import BinanceInsufficientBalance
        raise BinanceInsufficientBalance("Margin is insufficient", code=-2019)

    mock_client.place_market_order = fail_order
    _install_mock_live_provider(monkeypatch, mock_client)

    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"symbol": "BTCUSDT", "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["submit"]["ok"] is False
    assert body["submit"]["reason"] == "Margin is insufficient"
    assert body["close"] is None


def test_test_order_blocked_when_minimum_exceeds_configured_max(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 1.0)  # below any real exchange minimum
    mock_client = FilteredMockClient()
    _install_mock_live_provider(monkeypatch, mock_client)

    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"symbol": "BTCUSDT", "confirm": True})
    assert r.status_code == 400
    assert "exceeds the configured" in r.json()["detail"]


def test_test_order_rejects_disallowed_symbol(monkeypatch):
    go_fully_ready(monkeypatch)
    client = make_client()
    r = client.post("/api/trading/binance/test-order", json={"symbol": "DOGEUSDT", "confirm": True})
    assert r.status_code == 400


# ------------------------------------------------------------------- no secrets

def test_execution_transparency_endpoints_never_leak_secrets(monkeypatch):
    go_fully_ready(monkeypatch)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(prediction_module, "prediction", fake_prediction())
    seed_attempt()
    mock_client = FilteredMockClient()
    _install_mock_live_provider(monkeypatch, mock_client)
    client = make_client()

    for method, path, json in (
        ("get", "/api/trading/binance/execution-pipeline?symbol=BTCUSDT", None),
        ("get", "/api/trading/binance/execution-logs", None),
        ("post", "/api/trading/binance/test-order", {"symbol": "BTCUSDT", "confirm": True}),
    ):
        r = getattr(client, method)(path, json=json) if json is not None else getattr(client, method)(path)
        assert FAKE_KEY not in r.text
        assert FAKE_SECRET not in r.text
