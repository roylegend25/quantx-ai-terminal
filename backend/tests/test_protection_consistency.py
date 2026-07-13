"""Phase 30: fixes the exact reported bug - a live ETHUSDT position that is
genuinely PROTECTED via the Algo provider (TP @ 1742, SL @ 1872) was still
reported MISSING_TP_SL by the real risk gate / Execution Pipeline, because
real_risk_gate.py called protection.derive_protection() directly (classic
orders only) instead of the Algo-aware protection.resolve_protection() every
other consumer of protection status already used.

These tests cover: the shared resolver used consistently everywhere,
protection-revision bumping + cache invalidation on confirmed mutations,
tracked Algo id persistence, rate-limit-safe status, and the current-vs-
historical execution separation."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import (
    BinanceExecutionAttempt,
    ExchangePositionRow,
    TradingAuditLog,
    TradingControl,
)
from app.exchanges.binance_errors import BinanceError, BinanceRateLimitError
from app.exchanges.binance_models import BinanceBalance, BinanceOrder, BinancePosition
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes, protection, real_risk_gate
from tests.test_execution_router_binance import make_provider

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


def eth_short_position(symbol="ETHUSDT"):
    return BinancePosition(
        symbol=symbol, side="SHORT", quantity=0.038, entry_price=1800.49, mark_price=1799.30,
        leverage=10.0, margin_type="isolated", liquidation_price=1971.95,
        unrealized_pnl=0.05, margin_used=6.87, notional=68.36,
    )


class AlgoAwareClient:
    """A minimal Binance client double that supports BOTH classic orders
    (get_open_orders) and the Algo Order API lookup (GET /fapi/v1/algoOrder
    by algoId, signed=True) - the exact surface protection.resolve_protection
    needs to verify an Algo-protected position, matching the real ETHUSDT
    incident (TP algo order @ 1742, SL algo order @ 1872)."""

    configured = True
    read_only = True

    def __init__(self, positions=None, open_orders=None, algo_orders=None, fail_after=None):
        self.calls = 0
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.algo_orders = algo_orders or {}
        self._fail_after = fail_after

    async def get_account_info(self):
        from app.exchanges.binance_models import BinanceAccountSummary
        return BinanceAccountSummary(
            total_wallet_balance=1000.0, available_balance=1000.0, total_margin_balance=1000.0,
            total_unrealized_pnl=0.0, total_initial_margin=0.0, total_maintenance_margin=0.0,
        )

    async def get_positions(self, symbol=None):
        self.calls += 1
        if self._fail_after is not None and self.calls > self._fail_after:
            raise BinanceRateLimitError("Too many requests", status=429)
        return list(self.positions)

    async def get_open_orders(self, symbol=None):
        return list(self.open_orders)

    async def get_daily_realized_pnl(self):
        return 0.0

    async def get_balances(self):
        return [BinanceBalance(asset="USDT", balance=1000.0, available=1000.0)]

    async def ping(self):
        return True

    async def get_position_mode(self):
        return False

    async def _get(self, path, params=None, signed=False, priority=None):
        if path == "/fapi/v1/algoOrder":
            algo_id = int((params or {}).get("algoId"))
            raw = self.algo_orders.get(algo_id)
            if raw is None:
                raise BinanceError("Order does not exist", code=-2013, status=400)
            return raw
        raise AssertionError(f"unexpected GET {path}")


def algo_raw(algo_id: int, order_type: str, trigger_price: float, symbol="ETHUSDT", status="NEW"):
    return {
        "algoId": algo_id, "clientAlgoId": f"qx-{algo_id}", "algoType": "CONDITIONAL", "orderType": order_type,
        "symbol": symbol, "side": "BUY", "positionSide": "BOTH", "algoStatus": status,
        "triggerPrice": trigger_price, "quantity": 0.038,
    }


def seed_tracked_row(mode: str, symbol: str, *, tp_algo_id=None, sl_algo_id=None, provider="algo") -> None:
    db = SessionLocal()
    try:
        db.add(ExchangePositionRow(
            mode=mode, symbol=symbol, side="SHORT", quantity=0.038,
            protection_provider=provider, tp_algo_id=tp_algo_id, sl_algo_id=sl_algo_id,
        ))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.query(BinanceExecutionAttempt).delete()
        db.query(TradingAuditLog).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    snapshot_service.reset()
    protection.reset_algo_lookup_cache()
    real_risk_gate.reset_duplicate_guard()
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    yield
    modes.set_mode(modes.MODE_PAPER)


def run_gate(client, **kwargs):
    defaults = dict(symbol="BTCUSDT", side="LONG", notional_usdt=10.0, leverage=1.0, sl=45000.0, tp=55000.0)
    defaults.update(kwargs)
    return asyncio.run(real_risk_gate.evaluate_real_order(client=client, **defaults))


# ============================ the exact reported bug, fixed ================

def test_algo_protected_position_passes_existing_position_protected():
    """The exact incident: ETHUSDT SHORT 0.038, TP algo @ 1742, SL algo @
    1872, both NEW/active. Before the fix this reported MISSING_TP_SL
    because real_risk_gate used derive_protection() (classic orders only)."""
    modes.set_mode(modes.MODE_TESTNET)
    seed_tracked_row(modes.MODE_TESTNET, "ETHUSDT", tp_algo_id=201, sl_algo_id=202)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={
            201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0),
            202: algo_raw(202, "STOP_MARKET", 1872.0),
        },
    )
    result = run_gate(client)
    assert result.allowed, result.reason


def test_classic_protected_position_passes_existing_position_protected():
    modes.set_mode(modes.MODE_TESTNET)
    orders = [
        BinanceOrder(order_id=1, client_order_id="qxsl", symbol="ETHUSDT", side="BUY", position_side="BOTH",
                    type="STOP_MARKET", status="NEW", price=0.0, stop_price=1872.0, quantity=0.038,
                    executed_qty=0.0, avg_price=0.0, reduce_only=True, close_position=False),
        BinanceOrder(order_id=2, client_order_id="qxtp", symbol="ETHUSDT", side="BUY", position_side="BOTH",
                    type="TAKE_PROFIT_MARKET", status="NEW", price=0.0, stop_price=1742.0, quantity=0.038,
                    executed_qty=0.0, avg_price=0.0, reduce_only=True, close_position=False),
    ]
    client = AlgoAwareClient(positions=[eth_short_position()], open_orders=orders)
    result = run_gate(client)
    assert result.allowed, result.reason


def test_missing_take_profit_only_fails_existing_position_protected():
    modes.set_mode(modes.MODE_TESTNET)
    seed_tracked_row(modes.MODE_TESTNET, "ETHUSDT", sl_algo_id=202)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={202: algo_raw(202, "STOP_MARKET", 1872.0)},
    )
    result = run_gate(client)
    assert not result.allowed
    assert "MISSING_TP" in result.reason


def test_missing_stop_loss_only_fails_existing_position_protected():
    modes.set_mode(modes.MODE_TESTNET)
    seed_tracked_row(modes.MODE_TESTNET, "ETHUSDT", tp_algo_id=201)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0)},
    )
    result = run_gate(client)
    assert not result.allowed
    assert "MISSING_SL" in result.reason


# ==================================== one shared resolver, consistent everywhere

def test_watchdog_style_check_and_risk_gate_agree_on_algo_protected_position():
    """protection_watchdog._scan_once() and real_risk_gate both ultimately
    call protection.resolve_protection() - verify they report the SAME
    status for the same underlying data instead of each computing it
    independently."""
    modes.set_mode(modes.MODE_TESTNET)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0), 202: algo_raw(202, "STOP_MARKET", 1872.0)},
    )

    watchdog_style = asyncio.run(protection.resolve_protection(
        client, "ETHUSDT", tp_algo_id=201, sl_algo_id=202,
    ))

    seed_tracked_row(modes.MODE_TESTNET, "ETHUSDT", tp_algo_id=201, sl_algo_id=202)
    gate_result = run_gate(client)

    assert watchdog_style.status == protection.PROTECTED
    assert gate_result.allowed, gate_result.reason


def test_portfolio_positions_and_risk_gate_agree_on_algo_protected_position(monkeypatch):
    # app.api.portfolio.get_read_client() is always the PRODUCTION account
    # (see its own docstring), so binance_positions looks up tracked Algo
    # ids under MODE_LIVE regardless of the active bot mode - matching the
    # real reported incident, which was on the live account.
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.unlock_live()
    modes.set_mode(modes.MODE_LIVE)
    seed_tracked_row(modes.MODE_LIVE, "ETHUSDT", tp_algo_id=201, sl_algo_id=202)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0), 202: algo_raw(202, "STOP_MARKET", 1872.0)},
    )
    monkeypatch.setattr(portfolio_module, "_read_client", client)

    app = FastAPI()
    app.include_router(portfolio_module.router)
    portfolio_positions = TestClient(app).get("/api/portfolio/binance/positions").json()["positions"]
    assert portfolio_positions[0]["protection_status"] == "PROTECTED"

    gate_result = run_gate(client)
    assert gate_result.allowed, gate_result.reason


# ============================== revision + cache invalidation ===============

def test_successful_protect_invalidates_snapshot_immediately(monkeypatch):
    from tests.test_execution_router_binance import MockBinanceClient
    from tests.test_portfolio_api import MockReadClient

    modes.set_mode(modes.MODE_TESTNET)
    write_client = MockBinanceClient(position=eth_short_position())
    monkeypatch.setattr(tc.execution_router, "_testnet", make_provider(write_client))

    app = FastAPI()
    app.include_router(tc.router)
    api_client = TestClient(app)

    # populate the shared snapshot cache first (as a normal page load would,
    # via the separate read-only client) - the /protect endpoint writes
    # through a different client, but invalidate_after_protection_mutation()
    # must still clear this cache regardless of which client populated it.
    read_client = MockReadClient()
    asyncio.run(snapshot_service.get_account_bundle(read_client))
    asyncio.run(snapshot_service.get_open_orders(read_client))
    assert snapshot_service.section_meta()["account"]["age_seconds"] is not None

    r = api_client.post("/api/trading/binance/positions/ETHUSDT/protect",
                        json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r.status_code == 200
    body = r.json()
    assert body["protection"]["status"] == "PROTECTED"
    assert body["snapshot_invalidated"] is True

    # the mutation must invalidate the cache synchronously, not wait for TTL
    assert snapshot_service.section_meta()["account"]["age_seconds"] is None
    assert snapshot_service.section_meta()["orders"]["age_seconds"] is None


def test_protection_revision_increments_on_confirmed_mutation(monkeypatch):
    from tests.test_execution_router_binance import MockBinanceClient

    modes.set_mode(modes.MODE_TESTNET)
    write_client = MockBinanceClient(position=eth_short_position())
    monkeypatch.setattr(tc.execution_router, "_testnet", make_provider(write_client))

    app = FastAPI()
    app.include_router(tc.router)
    api_client = TestClient(app)

    r1 = api_client.post("/api/trading/binance/positions/ETHUSDT/protect",
                         json={"take_profit": 1792.0, "stop_loss": 1810.0})
    assert r1.status_code == 200

    db = SessionLocal()
    try:
        row = db.query(ExchangePositionRow).filter_by(mode=modes.MODE_TESTNET, symbol="ETHUSDT").first()
        first_revision = row.protection_revision
        assert first_revision and first_revision >= 1
        assert row.protection_status == "PROTECTED"
        assert row.protection_verified_at is not None
    finally:
        db.close()

    r2 = api_client.post("/api/trading/binance/positions/ETHUSDT/protect",
                         json={"take_profit": 1795.0, "stop_loss": 1815.0})
    assert r2.status_code == 200

    db = SessionLocal()
    try:
        row = db.query(ExchangePositionRow).filter_by(mode=modes.MODE_TESTNET, symbol="ETHUSDT").first()
        assert row.protection_revision > first_revision
    finally:
        db.close()


# ==================================== persisted algo tracking (restart-safe)

def test_tracked_algo_ids_survive_a_fresh_db_session():
    """Simulates a backend restart: tracked Algo ids are a database row
    (app.db.models.ExchangePositionRow), never an in-memory dict - a brand
    new SessionLocal() (as a fresh process would open) must still see them."""
    modes.set_mode(modes.MODE_LIVE)
    seed_tracked_row(modes.MODE_LIVE, "ETHUSDT", tp_algo_id=301, sl_algo_id=302)

    db = SessionLocal()  # a completely new session, as if the process just started
    try:
        row = db.query(ExchangePositionRow).filter_by(mode=modes.MODE_LIVE, symbol="ETHUSDT").first()
        assert row is not None
        assert row.tp_algo_id == 301
        assert row.sl_algo_id == 302
        assert row.protection_provider == "algo"
    finally:
        db.close()


# ============================== rate-limit-safe status ======================

def test_rate_limited_protection_status_preserves_last_confirmed_state(monkeypatch):
    modes.set_mode(modes.MODE_TESTNET)
    seed_tracked_row(modes.MODE_TESTNET, "ETHUSDT", tp_algo_id=201, sl_algo_id=202)
    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0), 202: algo_raw(202, "STOP_MARKET", 1872.0)},
        fail_after=1,  # first get_positions() succeeds, every one after is rate-limited
    )
    monkeypatch.setattr(portfolio_module, "_read_client", client)

    app = FastAPI()
    app.include_router(tc.router)
    api_client = TestClient(app)

    first = api_client.get("/api/trading/binance/protection-status").json()
    assert first["positions"][0]["status"] == "PROTECTED"

    # force the next refresh to hit the client's simulated rate limit
    asyncio.run(snapshot_service.get_account_bundle(client, force_refresh=True))

    second = api_client.get("/api/trading/binance/protection-status").json()
    assert second["positions"][0]["symbol"] == "ETHUSDT"
    assert second["positions"][0]["status"] == "PROTECTED"  # never downgraded to MISSING_TP_SL
    assert second["positions"][0]["stale"] is True


# ============================ current vs historical execution ===============

def test_historical_execution_failure_does_not_change_current_protection_check(monkeypatch):
    """A stale BinanceExecutionAttempt row recorded 'unprotected' hours ago
    must not make the CURRENT protection_check (computed fresh) report
    unprotected now that the position is actually protected."""
    import app.api.prediction as prediction_module

    modes.set_mode(modes.MODE_LIVE)
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    modes.unlock_live()
    seed_tracked_row(modes.MODE_LIVE, "ETHUSDT", tp_algo_id=201, sl_algo_id=202)

    client = AlgoAwareClient(
        positions=[eth_short_position()], open_orders=[],
        algo_orders={201: algo_raw(201, "TAKE_PROFIT_MARKET", 1742.0), 202: algo_raw(202, "STOP_MARKET", 1872.0)},
    )
    monkeypatch.setattr(portfolio_module, "_read_client", client)

    db = SessionLocal()
    try:
        db.add(BinanceExecutionAttempt(
            mode=modes.MODE_LIVE, symbol="ETHUSDT", side="SHORT", is_test=False,
            stages=[{"stage": "risk_gate", "status": "failed",
                    "reason": "Existing live position is unprotected", "at": "2026-07-13T03:40:00+00:00"}],
            final_status="failed",
            final_reason="Existing live position is unprotected (ETHUSDT MISSING_TP_SL)",
        ))
        db.commit()
    finally:
        db.close()

    async def fake_prediction(symbol, interval="5m"):
        return {
            "direction": "SHORT", "confidence": 30.0, "target": 1700.0, "stop": 1900.0,
            "decision_engine": {"required_confidence": 45.0, "active_model": {}, "model_votes": []},
            "prediction": {"computed_at": 1750000000000},
        }

    monkeypatch.setattr(prediction_module, "prediction", fake_prediction)

    app = FastAPI()
    app.include_router(tc.router)
    api_client = TestClient(app)

    pipeline = api_client.get("/api/trading/binance/execution-pipeline?symbol=ETHUSDT").json()

    # the historical attempt is still visible as history...
    assert pipeline["final_reason"] == "Existing live position is unprotected (ETHUSDT MISSING_TP_SL)"
    assert pipeline["is_historical_attempt"] is True
    # ...but the CURRENT protection check (fresh, section 7-8) must pass,
    # and the current blocker must be confidence, not stale protection.
    assert pipeline["current_protection_check"]["passed"] is True
    decision = api_client.get("/api/trading/binance/decision-status?symbol=ETHUSDT").json()
    assert decision["protection_check"]["passed"] is True
    assert any("Confidence" in r for r in decision["blocked_reasons"])
    assert not any("unprotected" in r.lower() for r in decision["blocked_reasons"])
