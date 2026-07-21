"""/api/bot/status must reflect the real scheduler and automated-trade
state rather than a decorative flag - see app.trading.scheduler.RUNNING
and the Trading Horizon decision_id/authority_id/execution_mode provenance
recorded on Trade."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.bot as bot_module
from app.db.session import SessionLocal
from app.db.models import Trade
from app.trading import scheduler as trading_scheduler


@pytest.fixture(autouse=True)
def clean_trades():
    db = SessionLocal()
    try:
        db.query(Trade).filter(Trade.symbol == "STATUSTEST").delete()
        db.commit()
    finally:
        db.close()
    trading_scheduler.LAST_CYCLE_AT = None
    yield
    trading_scheduler.LAST_CYCLE_AT = None


def make_client():
    app = FastAPI()
    app.include_router(bot_module.router)
    app.dependency_overrides[bot_module.get_current_user] = lambda: "admin"
    return TestClient(app)


def test_status_reports_no_open_automatic_positions_by_default():
    client = make_client()
    body = client.get("/api/bot/status").json()
    assert body["open_automatic_positions"] == 0
    assert body["last_automatic_trade_id"] is None
    assert body["scheduler_last_cycle"] is None
    assert body["scheduler_next_cycle"] is None


def test_status_counts_open_automatic_positions():
    db = SessionLocal()
    try:
        db.add(Trade(symbol="STATUSTEST", side="LONG", entry=100.0, qty=1.0, status="OPEN",
                      execution_mode="automatic", decision_id="d1", authority_id="a1",
                      opened_at=datetime.now(timezone.utc)))
        db.commit()
    finally:
        db.close()
    client = make_client()
    body = client.get("/api/bot/status").json()
    assert body["open_automatic_positions"] == 1
    assert body["last_automatic_trade_id"] is not None
    assert body["last_automatic_trade_at"] is not None


def test_status_reflects_real_scheduler_cycle_timestamp():
    trading_scheduler.LAST_CYCLE_AT = "2026-07-16T12:00:00+00:00"
    client = make_client()
    body = client.get("/api/bot/status").json()
    assert body["scheduler_last_cycle"] == "2026-07-16T12:00:00+00:00"
    assert body["scheduler_next_cycle"] is not None
