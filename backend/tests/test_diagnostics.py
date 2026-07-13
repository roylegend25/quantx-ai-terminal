"""Trading Diagnostics (app/trading/diagnostics.py, Phase 28) - the
read-only investigation surface for the -4120 "use the Algo Order API"
finding. Every diagnostic field must degrade gracefully (never crash the
whole response) when a single Binance sub-call fails, and nothing here may
place a real order except through Binance's own /order/test endpoint."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.trading_control as tc
from app.db.session import SessionLocal
from app.db.models import BinanceBotTrade, BinanceExecutionAttempt, BinanceProtectionCapability, TradingControl
from app.trading import diagnostics, modes, real_risk_gate
from tests.test_execution_router_binance import make_provider

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


class DiagnosticsMockClient:
    configured = True
    read_only = False
    base_url = "https://fapi.binance.com"
    testnet = False

    def __init__(self, account=None, position_mode=None, positions=None, fail_permissions=False,
                fail_algo=False, exchange_info=None):
        self._account = account or {
            "feeTier": 0, "canTrade": True, "canDeposit": True, "canWithdraw": True, "tradeGroupId": -1,
        }
        self._position_mode = position_mode if position_mode is not None else {"dualSidePosition": False}
        self._positions = positions or []
        self._fail_permissions = fail_permissions
        self._fail_algo = fail_algo
        self._exchange_info = exchange_info or {
            "symbols": [{"symbol": "ETHUSDT", "orderTypes": ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]}]
        }

    async def _get(self, path, params=None, signed=False):
        if path == "/fapi/v2/account":
            return self._account
        if path == "/fapi/v1/positionSide/dual":
            return self._position_mode
        if path == "/fapi/v1/exchangeInfo":
            return self._exchange_info
        raise AssertionError(f"unexpected path {path}")

    async def get_multi_assets_margin(self):
        return False

    async def get_positions(self, symbol=None):
        return self._positions

    async def get_open_orders(self, symbol=None):
        return []

    async def get_position_mode(self):
        return bool(self._position_mode.get("dualSidePosition"))

    async def get_api_key_permissions(self):
        if self._fail_permissions:
            raise RuntimeError("apiRestrictions not permitted for this key")
        return {"enableFutures": True, "ipRestrict": True}

    async def probe_algo_api_reachable(self):
        if self._fail_algo:
            from app.exchanges.binance_errors import BinanceOrderRejected
            raise BinanceOrderRejected("Invalid API-key, IP, or permissions for action.", code=-2015)
        return {
            "reachable": True,
            "evidence": "Binance validated the request and rejected it for a business reason "
                        "(code=-1102, status=400: Param 'algoid' or 'clientalgoid' must be sent, "
                        "but both were empty/null!), not a 404 - the endpoint exists and is reachable "
                        "by this API key",
        }

    async def test_stop_market_order(self, symbol, side, stop_price, quantity):
        return {
            "ok": False, "request": {"symbol": symbol, "side": side, "type": "STOP_MARKET", "stopPrice": stop_price},
            "code": -4120, "status": 400,
            "message": "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.",
        }


# ------------------------------------------------------------- pure module

def test_account_diagnostics_reports_one_way_mode():
    client = DiagnosticsMockClient()
    result = asyncio.run(diagnostics.build_account_diagnostics(client))
    assert result["position_mode"] == "ONE-WAY"
    assert result["environment"] == "PRODUCTION"
    assert result["account_fields"]["trade_group_id"] == -1
    assert "standard retail account" in result["account_fields"]["trade_group_id_interpretation"]


def test_account_diagnostics_reports_hedge_mode():
    client = DiagnosticsMockClient(position_mode={"dualSidePosition": True})
    result = asyncio.run(diagnostics.build_account_diagnostics(client))
    assert result["position_mode"] == "HEDGE"


def test_account_diagnostics_degrades_gracefully_when_permissions_unavailable():
    client = DiagnosticsMockClient(fail_permissions=True)
    result = asyncio.run(diagnostics.build_account_diagnostics(client))
    assert result["api_permissions"] is None
    assert result["api_permissions_error"] is not None
    # the rest of the response is still populated
    assert result["position_mode"] == "ONE-WAY"


# ------------------------------------------ Debug 2.pdf section 7/8: position mode

def test_ensure_position_mode_passes_with_no_expectation():
    client = DiagnosticsMockClient()
    result = asyncio.run(diagnostics.ensure_position_mode_for_trading(client))
    assert result["status"] == "PASS"
    assert result["current_mode"] == "ONE-WAY"


def test_ensure_position_mode_passes_when_already_matching():
    client = DiagnosticsMockClient(position_mode={"dualSidePosition": False})
    result = asyncio.run(diagnostics.ensure_position_mode_for_trading(client, expected_hedge_mode=False))
    assert result["status"] == "PASS"


def test_ensure_position_mode_blocks_mismatch_with_open_positions():
    """The exact rule this bug is named after: never change position mode
    while any position or open order exists."""
    from app.exchanges.binance_models import BinancePosition

    client = DiagnosticsMockClient(
        position_mode={"dualSidePosition": False},
        positions=[BinancePosition(
            symbol="ETHUSDT", side="SHORT", quantity=0.05, entry_price=3000.0, mark_price=2990.0,
            leverage=2.0, margin_type="isolated", liquidation_price=4400.0,
            unrealized_pnl=0.5, margin_used=75.0, notional=149.5,
        )],
    )
    result = asyncio.run(diagnostics.ensure_position_mode_for_trading(client, expected_hedge_mode=True))

    assert result["status"] == "BLOCKED"
    assert result["mode_change_allowed"] is False
    assert result["open_positions_count"] == 1
    assert "Cancel orders/close positions first" in result["reason"]


def test_ensure_position_mode_allows_setup_when_account_is_empty():
    client = DiagnosticsMockClient(position_mode={"dualSidePosition": False})
    result = asyncio.run(diagnostics.ensure_position_mode_for_trading(client, expected_hedge_mode=True))

    assert result["status"] == "SETUP_REQUIRED"
    assert result["mode_change_allowed"] is True
    assert result["open_positions_count"] == 0
    assert result["open_orders_count"] == 0


def test_position_mode_diagnostics_reports_counts_and_allowed():
    from app.exchanges.binance_models import BinancePosition

    client = DiagnosticsMockClient(positions=[BinancePosition(
        symbol="ETHUSDT", side="SHORT", quantity=0.05, entry_price=3000.0, mark_price=2990.0,
        leverage=2.0, margin_type="isolated", liquidation_price=4400.0,
        unrealized_pnl=0.5, margin_used=75.0, notional=149.5,
    )])
    result = asyncio.run(diagnostics.build_position_mode_diagnostics(client))

    assert result["current_mode"] == "ONE-WAY"
    assert result["open_positions_count"] == 1
    assert result["open_orders_count"] == 0
    assert result["mode_change_allowed"] is False


def test_order_type_diagnostics_reports_documented_vs_actual_endpoint():
    client = DiagnosticsMockClient()
    result = asyncio.run(diagnostics.build_order_type_diagnostics(client, "ETHUSDT"))
    assert "STOP_MARKET" in result["order_types_per_exchange_info"]
    assert "algoOrder" in result["documented_algo_endpoint"]
    assert "REJECTED" in result["protective_endpoint_used_by_this_app"]
    assert result["algo_api_reachable"] is True


def test_order_type_diagnostics_degrades_when_algo_probe_fails():
    client = DiagnosticsMockClient(fail_algo=True)
    result = asyncio.run(diagnostics.build_order_type_diagnostics(client, "ETHUSDT"))
    assert result["algo_api_reachable"] is None
    assert result["algo_api_error"] is not None


def test_protection_provider_diagnostics_reports_undetected_as_auto():
    from app.trading import protection_capability

    db = SessionLocal()
    try:
        result = diagnostics.build_protection_provider_diagnostics(modes.MODE_TESTNET, db)
    finally:
        db.close()
    assert result["provider"] == "auto"
    assert result["capability"]["provider"] is None


def test_protection_provider_diagnostics_reports_detected_capability_and_last_response():
    from app.trading import protection_capability
    from app.db.models import BinanceBotTrade

    protection_capability.set_provider(modes.MODE_TESTNET, "algo", reason="TP leg: classic endpoint returned -4120")
    db = SessionLocal()
    try:
        db.query(BinanceBotTrade).delete()
        db.add(BinanceBotTrade(
            mode=modes.MODE_TESTNET, action="open", order_id=1, symbol="ETHUSDT", side="SHORT",
            order_side="SELL", order_type="MARKET", quantity=0.038, status="FILLED",
            provider_used="algo", provider_response={"tp": {"ok": True}, "sl": {"ok": True}},
            provider_latency_ms=42.5, verification_result="PROTECTED",
        ))
        db.commit()
        result = diagnostics.build_protection_provider_diagnostics(modes.MODE_TESTNET, db)
    finally:
        db.close()

    assert result["provider"] == "algo"
    assert "4120" in result["capability"]["detection_reason"]
    assert result["last_provider_used"] == "algo"
    assert result["last_algo_response"] == {"tp": {"ok": True}, "sl": {"ok": True}}
    assert result["last_provider_latency_ms"] == 42.5
    assert result["last_verification_result"] == "PROTECTED"


def test_last_protective_attempt_reports_no_data_when_none_recorded():
    db = SessionLocal()
    try:
        db.query(BinanceExecutionAttempt).delete()
        db.commit()
        result = diagnostics.last_protective_attempts(db)
    finally:
        db.close()
    assert result["available"] is False


def test_last_protective_attempt_reports_the_real_stage_detail():
    db = SessionLocal()
    try:
        db.query(BinanceExecutionAttempt).delete()
        db.add(BinanceExecutionAttempt(
            mode="BINANCE_LIVE", symbol="ETHUSDT", side="SHORT", final_status="PROTECTION_FAILED",
            final_reason="Entry filled but protection failed (MISSING_TP_SL): Order type not supported for this endpoint.",
            stages=[
                {"stage": "protective_tp_submitted", "status": "failed",
                 "reason": "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.", "at": "t"},
                {"stage": "protective_sl_submitted", "status": "failed",
                 "reason": "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.", "at": "t"},
                {"stage": "protection_confirmed", "status": "failed", "reason": "Verification found MISSING_TP_SL on Binance", "at": "t"},
            ],
        ))
        db.commit()
        result = diagnostics.last_protective_attempts(db)
    finally:
        db.close()
    assert result["available"] is True
    assert result["protective_tp_stage"]["status"] == "failed"
    assert "Algo Order API" in result["protective_tp_stage"]["reason"]


# ---------------------------------------------------------------- API layer

@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(BinanceExecutionAttempt).delete()
        db.query(BinanceProtectionCapability).delete()
        db.query(BinanceBotTrade).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    modes.set_mode(modes.MODE_TESTNET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def install_client(monkeypatch, client):
    monkeypatch.setattr(tc.execution_router, "_testnet", make_provider(client))
    return client


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def test_diagnostics_endpoint_returns_full_report(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    install_client(monkeypatch, DiagnosticsMockClient())
    client = make_client()
    body = client.get("/api/trading/binance/diagnostics?symbol=ETHUSDT").json()

    assert body["available"] is True
    assert body["account"]["position_mode"] == "ONE-WAY"
    assert body["order_types"]["symbol"] == "ETHUSDT"
    assert body["protection_provider"]["provider"] == "auto"
    assert body["last_protective_attempt"]["available"] is False
    assert body["config"]["require_tp_sl"] is True
    # Debug 2.pdf section 11
    assert body["position_mode"]["current_mode"] == "ONE-WAY"
    assert body["position_mode"]["open_positions_count"] == 0
    assert body["position_mode"]["open_orders_count"] == 0
    assert body["position_mode"]["mode_change_allowed"] is True
    assert body["rate_limit"]["source"] == "rate_limiter"
    assert "watchdog_interval_seconds" in body["rate_limit"]


def test_diagnostics_unavailable_when_not_configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client()
    body = client.get("/api/trading/binance/diagnostics").json()
    assert body["available"] is False


def test_diagnostics_never_leaks_secrets(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    install_client(monkeypatch, DiagnosticsMockClient())
    client = make_client()
    r = client.get("/api/trading/binance/diagnostics?symbol=ETHUSDT")
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


def test_test_stop_order_requires_live_mode():
    client = make_client()
    r = client.post("/api/trading/binance/diagnostics/test-stop-order",
                    json={"symbol": "ETHUSDT", "side": "BUY", "stop_price": 1810.0, "quantity": 0.038})
    assert r.status_code == 409


def test_test_stop_order_captures_the_real_4120_response(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(tc.execution_router, "_live", make_provider(DiagnosticsMockClient()))
    modes.unlock_live()
    modes.set_mode(modes.MODE_LIVE)

    client = make_client()
    r = client.post("/api/trading/binance/diagnostics/test-stop-order",
                    json={"symbol": "ETHUSDT", "side": "BUY", "stop_price": 1810.0, "quantity": 0.038})
    assert r.status_code == 200
    body = r.json()
    assert body["endpoint"] == "POST /fapi/v1/order/test"
    assert body["ok"] is False
    assert body["code"] == -4120
    assert "Algo Order API" in body["message"]
    # the redacted request is present with no signature/key
    assert "signature" not in body["request"]
    assert "apiKey" not in body["request"]
