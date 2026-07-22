"""Binance Real dry-run (never touches the real API): a fully-approved
Trading Horizon authority, run through the exact same dispatcher a real
automatic execution would use, with the mode set to BINANCE_LIVE while
BINANCE_LIVE_ENABLED stays false (the only state this repo ever runs real
mode in during this task). Proves the fully-valid decision reaches the
live-authorization gate and is blocked there - never reaching the mocked
Binance client, and never producing the generic "Model has not produced an
actionable signal" message that a NO_TRADE/unevaluated decision would."""
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.trading import modes
from app.trading.execution_router import BinanceExecutionProvider, router as execution_router

from tests.test_horizon_authority import authority, persisted_decision, setup_user


class _RaisingClient:
    """Any real network/write call here fails the test outright - this
    dry-run must never reach the exchange client at all."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("BinanceFuturesClient must never be constructed during a live-disabled dry-run")


@pytest.fixture(autouse=True)
def _reset_mode():
    setup_user()
    modes.set_mode(modes.MODE_PAPER)
    yield
    modes.set_mode(modes.MODE_PAPER)


def test_fully_approved_decision_blocked_at_live_lock_never_reaches_client():
    assert settings.binance_live_enabled is False, "This dry-run only proves anything when live is actually disabled"
    decision = persisted_decision()

    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED

    with patch("app.exchanges.binance_futures_client.BinanceFuturesClient", _RaisingClient):
        import asyncio
        result = asyncio.run(execution_router.open_position(**authority(decision)))

    assert result.ok is False
    assert result.mode == modes.MODE_LIVE_LOCKED
    # Never the generic actionable-signal message - this decision was fully
    # valid; the block is specifically the live-authorization gate.
    assert result.reason != "Model has not produced an actionable signal"
    assert "lock" in (result.reason or "").lower() or "live" in (result.reason or "").lower()

    # The real provider's client must never have been instantiated - proves
    # zero calls reached anything that could touch the real Binance API.
    live_provider = execution_router._live
    assert live_provider is None, "BinanceExecutionProvider(testnet=False) must never be constructed on this path"


def test_live_provider_client_construction_itself_is_blocked_if_ever_reached():
    """Defense in depth: even if a future change removed the router's
    early _blocked() short-circuit, constructing the real client directly
    while BINANCE_LIVE_ENABLED is false must still refuse."""
    from app.exchanges.binance_futures_client import BinanceFuturesClient

    assert settings.binance_live_enabled is False
    with pytest.raises(Exception):
        BinanceFuturesClient(testnet=False, read_only=False)
