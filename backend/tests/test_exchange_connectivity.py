"""Binance connectivity consistency (Bot Settings / Binance Real page fix):
GET /api/trading/mode and GET /api/exchange/status must agree on whether
the signed account read actually succeeded, distinguishing that from mere
public-market reachability and from key configuration. Covers the exact
bug this closes - `binance_connected` used to be silently absent from
/api/trading/mode (the endpoint nearly every page polls), so pages showed
"Account Connected: No" even when the signed read was fine."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.exchange as exchange_module
import app.api.portfolio as portfolio_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import TradingAuditLog, TradingControl
from app.exchanges.binance_errors import BinanceNetworkError, BinancePermissionError, BinanceTimestampError
from tests.test_portfolio_api import FAKE_KEY, FAKE_SECRET, MockReadClient, configure_keys

pytestmark = pytest.mark.filterwarnings("ignore")


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
    exchange_module._connected_cache = (0.0, False)
    exchange_module._account_probe_cache = (0.0, {"connected": False, "error": None})
    exchange_module._public_ping_cache = (0.0, False)
    yield


class FailingReadClient:
    """Read-client double whose signed call always raises a given error -
    lets tests exercise _classify_binance_error without a real Binance
    exchange rejection."""

    configured = True
    read_only = True

    def __init__(self, error: Exception):
        self._error = error

    async def get_balances(self):
        raise self._error


def make_exchange_client():
    app = FastAPI()
    app.include_router(exchange_module.router)
    return TestClient(app)


def make_trading_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def mock_public_ping(monkeypatch, value: bool):
    async def fake(*_a, **_kw):
        return value

    monkeypatch.setattr(exchange_module, "_binance_public_connected", fake)


# --------------------------------------------------- binance_connectivity()

def _run(coro):
    return asyncio.run(coro)


def test_connectivity_true_when_signed_read_succeeds(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", MockReadClient())
    result = _run(exchange_module.binance_connectivity(public_connected=True))
    assert result["binance_api_key_configured"] is True
    assert result["binance_api_secret_configured"] is True
    assert result["binance_public_connected"] is True
    assert result["binance_account_connected"] is True
    assert result["binance_signed_read_ok"] is True
    assert result["binance_account_error"] is None
    assert result["binance_connected"] is True


def test_connectivity_false_with_safe_reason_when_signed_read_fails(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinancePermissionError("x", code=-2015)))
    result = _run(exchange_module.binance_connectivity(public_connected=True))
    assert result["binance_account_connected"] is False
    assert result["binance_signed_read_ok"] is False
    assert result["binance_account_error"] == "Futures permission or IP whitelist issue"
    assert result["binance_connected"] is False
    # public reachability is independent of the signed-read failure
    assert result["binance_public_connected"] is True


def test_connectivity_classifies_timestamp_and_network_errors(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinanceTimestampError("x")))
    result = _run(exchange_module.binance_connectivity(public_connected=True))
    assert result["binance_account_error"] == "Timestamp drift"

    exchange_module._account_probe_cache = (0.0, {"connected": False, "error": None})
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinanceNetworkError("x")))
    result = _run(exchange_module.binance_connectivity(public_connected=True))
    assert result["binance_account_error"] == "Binance unavailable"


def test_connectivity_not_configured_is_false_with_no_error(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    result = _run(exchange_module.binance_connectivity(public_connected=False))
    assert result["binance_api_key_configured"] is False
    assert result["binance_account_connected"] is False
    assert result["binance_account_error"] is None


# ----------------------------------------------------- GET /api/exchange/status

def test_exchange_status_reflects_signed_account_connected(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", MockReadClient())
    mock_public_ping(monkeypatch, True)
    monkeypatch.setattr(exchange_module.manager, "status_all", _fake_status_all(True))

    client = make_exchange_client()
    body = client.get("/api/exchange/status").json()
    assert body["binance_account_connected"] is True
    assert body["binance_connected"] is True
    assert body["binance_signed_read_ok"] is True


def test_exchange_status_false_when_signed_read_fails(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinancePermissionError("x", code=-2014)))
    monkeypatch.setattr(exchange_module.manager, "status_all", _fake_status_all(True))

    client = make_exchange_client()
    body = client.get("/api/exchange/status").json()
    assert body["binance_account_connected"] is False
    assert body["binance_signed_read_ok"] is False
    assert body["binance_account_error"] == "Invalid key"
    # public data can still be reachable even though the signed read failed
    assert body["binance_public_connected"] is True


def test_exchange_status_never_leaks_secrets(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinancePermissionError("x", code=-2015)))
    monkeypatch.setattr(exchange_module.manager, "status_all", _fake_status_all(False))

    client = make_exchange_client()
    r = client.get("/api/exchange/status")
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


# ---------------------------------------------------------- GET /api/trading/mode

def test_trading_mode_endpoint_includes_connectivity_fields(monkeypatch):
    """The actual bug: /api/trading/mode (TradingShared.useTradingStatus,
    used by BinanceRealPage/BotSettingsPage/ExecutionPage) used to omit
    binance_connected entirely - always falsy regardless of real status."""
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", MockReadClient())
    mock_public_ping(monkeypatch, True)

    client = make_trading_client()
    body = client.get("/api/trading/mode").json()
    assert body["binance_connected"] is True
    assert body["binance_account_connected"] is True
    assert body["binance_signed_read_ok"] is True
    assert body["binance_public_connected"] is True
    assert body["binance_api_key_configured"] is True
    assert body["binance_api_secret_configured"] is True


def test_trading_mode_endpoint_false_when_signed_read_fails(monkeypatch):
    configure_keys(monkeypatch)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinancePermissionError("x", code=-2015)))
    mock_public_ping(monkeypatch, True)

    client = make_trading_client()
    body = client.get("/api/trading/mode").json()
    assert body["binance_connected"] is False
    assert body["binance_account_connected"] is False
    assert body["binance_account_error"] == "Futures permission or IP whitelist issue"


def test_trading_mode_endpoint_never_leaks_secrets(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(portfolio_module, "_read_client", FailingReadClient(BinanceTimestampError("x")))
    mock_public_ping(monkeypatch, False)

    client = make_trading_client()
    r = client.get("/api/trading/mode")
    assert r.status_code == 200
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


def _fake_status_all(binance_public_connected: bool):
    async def fake(*_a, **_kw):
        return {"read_only": True, "exchanges": {"binance": {"connected": binance_public_connected, "configured": True}}}

    return fake
