import asyncio
import json

from app.api import exchange
from app.core.config import settings


def test_credential_status_is_safe_and_connected(monkeypatch):
    key, secret = "test-key-value", "test-secret-value"
    monkeypatch.setattr(settings, "binance_api_key", key)
    monkeypatch.setattr(settings, "binance_api_secret", secret)
    monkeypatch.setattr(settings, "binance_futures_testnet", False)

    async def connected(public_connected=None):
        return {"binance_signed_read_ok": True, "binance_account_error": None}
    monkeypatch.setattr(exchange, "binance_connectivity", connected)
    payload = asyncio.run(exchange.binance_credential_status())
    encoded = json.dumps(payload)
    assert payload["configured"] is True
    assert payload["connection_valid"] is True
    assert payload["permissions_valid"] is True
    assert payload["environment"] == "real"
    assert key not in encoded and secret not in encoded


def test_missing_credentials_have_no_secret_metadata(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    async def disconnected(public_connected=None):
        return {"binance_signed_read_ok": False, "binance_account_error": None}
    monkeypatch.setattr(exchange, "binance_connectivity", disconnected)
    payload = asyncio.run(exchange.binance_credential_status())
    assert payload["configured"] is False
    assert payload["last_verified_at"] is None
