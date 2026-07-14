"""GET /api/binance/snapshot and POST /api/binance/snapshot/refresh (Phase
29) - the one endpoint every page should poll instead of assembling its
own view from several separate /api/portfolio/binance/* calls."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.binance_snapshot as snapshot_api
import app.api.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.db.models import ExchangePositionRow, TradingControl
from app.exchanges.binance_errors import BinanceRateLimitError
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes, protection_watchdog
from tests.test_portfolio_api import FAKE_KEY, FAKE_SECRET, MockReadClient, use_mock_read_client


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(ExchangePositionRow).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    snapshot_service.reset()
    protection_watchdog.LAST_SCAN = {
        "checked_at": None, "unprotected": [], "repair_failed": [], "critical": False,
        "available": True, "reason": None,
    }
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(snapshot_api.router)
    return TestClient(app)


def test_snapshot_shape_and_no_secrets(monkeypatch):
    use_mock_read_client(monkeypatch)
    client = make_client()

    r = client.get("/api/binance/snapshot")
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text
    body = r.json()
    for key in ("updated_at", "stale", "rate_limited", "retry_after_seconds", "account",
                "balances", "positions", "orders", "income", "protection", "readiness", "errors",
                "positions_available"):
        assert key in body
    assert body["positions"][0]["symbol"] == "ETHUSDT"
    assert body["positions_available"] is True


# ------------------------------- Debug 3.pdf: Dashboard real-position shape

def test_snapshot_positions_are_self_describing(monkeypatch):
    """Every position in the shared snapshot must carry its own source/
    stale/updated_at - the Dashboard's Open Positions card reads these
    directly off each position instead of re-deriving them, and must never
    mistake a Binance real position for a paper one."""
    use_mock_read_client(monkeypatch)
    client = make_client()

    body = client.get("/api/binance/snapshot").json()
    pos = body["positions"][0]
    assert pos["source"] == "binance_real"
    assert pos["stale"] is False
    assert pos["updated_at"] == body["updated_at"]
    # the full rich position shape (protection, TP/SL, liquidation, ...) is
    # still present, unchanged by adding the new fields
    assert pos["protection_status"] in ("PROTECTED", "MISSING_TP", "MISSING_SL", "MISSING_TP_SL")
    assert "liquidation_price" in pos
    assert "margin_used" in pos


def test_snapshot_positions_marked_stale_during_rate_limit(monkeypatch):
    calls = {"n": 0}

    class FlakyClient(MockReadClient):
        async def get_account_info(self):
            calls["n"] += 1
            if calls["n"] > 1:
                raise BinanceRateLimitError("Too many requests", status=429)
            return await super().get_account_info()

    use_mock_read_client(monkeypatch, client=FlakyClient())
    client = make_client()

    client.get("/api/binance/snapshot")  # warm the cache
    refreshed = client.post("/api/binance/snapshot/refresh").json()

    assert refreshed["positions"][0]["stale"] is True
    assert refreshed["positions"][0]["source"] == "binance_real"


def test_snapshot_positions_available_false_when_not_configured():
    client = make_client()  # no keys configured (autouse fixture doesn't set any)

    body = client.get("/api/binance/snapshot").json()
    assert body["positions_available"] is False
    assert body["positions"] == []


def test_snapshot_reflects_unprotected_positions(monkeypatch):
    use_mock_read_client(monkeypatch)  # ETHUSDT SHORT with a resting SL but no TP -> MISSING_TP
    client = make_client()

    body = client.get("/api/binance/snapshot").json()
    assert body["protection"]["unprotected"][0]["symbol"] == "ETHUSDT"
    assert body["protection"]["unprotected"][0]["status"] == "MISSING_TP"


def test_manual_refresh_never_wipes_positions_when_rate_limited(monkeypatch):
    """Section 13/15: a rate-limited manual sync must still return the last
    known good positions, marked stale, never an empty list."""
    calls = {"n": 0}

    class FlakyClient(MockReadClient):
        async def get_account_info(self):
            calls["n"] += 1
            if calls["n"] > 1:
                raise BinanceRateLimitError("Too many requests", status=429)
            return await super().get_account_info()

    use_mock_read_client(monkeypatch, client=FlakyClient())
    client = make_client()

    first = client.get("/api/binance/snapshot").json()
    assert len(first["positions"]) == 1

    refreshed = client.post("/api/binance/snapshot/refresh").json()
    assert len(refreshed["positions"]) == 1  # never wiped to []
    assert refreshed["stale"] is True


def test_five_concurrent_snapshot_requests_share_one_account_fetch(monkeypatch):
    call_count = {"account_info": 0}

    class CountingClient(MockReadClient):
        async def get_account_info(self):
            call_count["account_info"] += 1
            return await super().get_account_info()

    use_mock_read_client(monkeypatch, client=CountingClient())

    async def five_concurrent():
        await asyncio.gather(*[snapshot_api.get_snapshot(db=SessionLocal()) for _ in range(5)])

    asyncio.run(five_concurrent())
    assert call_count["account_info"] == 1
