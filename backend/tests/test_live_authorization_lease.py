"""Phase 31 permanent defense-in-depth: the short-lived live-authorization
lease that replaced the persistent live_unlocked boolean after a real
incident (two real orders filled 2026-07-15, sourced from an unlock granted
2026-07-11 that never expired). Every test here proves no single setting -
env flag, DB mode, or the old unlock boolean alone - can authorize a real
order, and that the lease itself cannot survive a restart, an emergency
stop, or more than its configured action budget.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import LiveAuthorizationLease, TradingControl
from app.exchanges.binance_futures_client import BinanceFuturesClient, LiveTradingLocked
from app.exchanges.binance_models import BinanceBalance
from app.trading import modes, real_risk_gate
from app.trading.execution_router import BinanceExecutionProvider, ExecutionRouter, PaperExecutionProvider

FAKE_LIVE_KEY = "AKIALIVEKEY1234567890"
FAKE_LIVE_SECRET = "livesecretvalue0987654321"


@pytest.fixture(autouse=True)
def clean_state():
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(LiveAuthorizationLease).delete()
        db.commit()
    finally:
        db.close()
    real_risk_gate.reset_duplicate_guard()
    yield


class HealthyClient:
    """A well-funded, reachable account - and a tripwire: if the live gate
    is ever bypassed, any of these methods being called at all proves it,
    regardless of what they'd return."""

    called = False

    async def ping(self):
        self.called = True
        return True

    async def get_account_info(self):
        self.called = True
        from app.exchanges.binance_models import BinanceAccountSummary
        return BinanceAccountSummary(
            total_wallet_balance=1000.0, available_balance=1000.0, total_margin_balance=1000.0,
            total_unrealized_pnl=0.0, total_initial_margin=0.0, total_maintenance_margin=0.0,
        )

    async def get_daily_realized_pnl(self):
        self.called = True
        return 0.0

    async def get_balances(self):
        self.called = True
        return [BinanceBalance(asset="USDT", balance=1000.0, available=1000.0)]

    async def get_positions(self, symbol=None):
        self.called = True
        return []

    async def get_open_orders(self, symbol=None):
        self.called = True
        return []


def run_gate(mode, client=None, **kwargs):
    defaults = dict(symbol="BTCUSDT", side="LONG", notional_usdt=10.0, leverage=1.0, sl=45000.0, tp=55000.0)
    defaults.update(kwargs)
    modes.set_mode(mode)
    return asyncio.run(real_risk_gate.evaluate_real_order(client=client or HealthyClient(), **defaults))


# --------------------------------------------------- 1 & 2: startup safety

def test_startup_forces_paper_mode_regardless_of_persisted_state():
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    db = SessionLocal()
    try:
        row = db.get(TradingControl, 1)
        row.mode = modes.MODE_LIVE
        row.live_unlocked = True
        db.commit()
    finally:
        db.close()

    modes.startup_safety_reset()

    assert modes.effective_mode() == modes.MODE_PAPER
    assert modes.get_control()["live_unlocked"] is False


def test_startup_clears_every_live_authorization_lease():
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    lease = modes.create_live_lease(user="admin")
    assert modes.get_active_lease() is not None

    modes.startup_safety_reset()

    assert modes.get_active_lease() is None
    db = SessionLocal()
    try:
        row = db.get(LiveAuthorizationLease, lease["id"])
        assert row.revoked is True
        assert row.revoked_reason == "startup_reset"
    finally:
        db.close()


def test_startup_logs_when_env_still_requests_live(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    # Should not raise / crash - the app must still start, just paper-only.
    result = modes.startup_safety_reset()
    assert result["mode"] == modes.MODE_PAPER
    db = SessionLocal()
    try:
        from app.db.models import TradingAuditLog
        assert db.query(TradingAuditLog).filter_by(event="startup_live_flag_detected").count() == 1
    finally:
        db.close()


# ------------------------------------------------- 3: no client-side state

def test_lease_state_is_server_side_only_not_cached_in_process():
    """Two independent reads (simulating two different browser sessions /
    requests after a refresh) see the exact same DB-backed truth - nothing
    about the lease is remembered in module-level state that a refreshed
    frontend could resurrect independently of the server."""
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user="admin")
    first_read = modes.get_active_lease()
    second_read = modes.get_active_lease()
    assert first_read == second_read
    modes.revoke_all_leases("test")
    assert modes.get_active_lease() is None


# --------------------------------------------------------- 4: expiry

def test_expired_lease_cannot_authorize_an_order(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    lease = modes.create_live_lease(user="admin")

    db = SessionLocal()
    try:
        row = db.get(LiveAuthorizationLease, lease["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()

    assert modes.get_active_lease() is None
    result = run_gate(modes.MODE_LIVE)
    assert not result.allowed
    assert "authorization lease" in result.reason.lower()

    # the expiry is itself audited, not silently dropped
    db = SessionLocal()
    try:
        from app.db.models import TradingAuditLog
        assert db.query(TradingAuditLog).filter_by(event="live_unlock_expired").count() == 1
    finally:
        db.close()


def test_lease_ttl_is_clamped_regardless_of_configuration(monkeypatch):
    monkeypatch.setattr(settings, "live_lease_ttl_seconds", 999_999)
    lease = modes.create_live_lease(user="admin")
    assert lease["seconds_remaining"] <= modes._MAX_LEASE_TTL_SECONDS


# ---------------------------------------------- 5: Emergency Stop revokes

def test_kill_switch_immediately_revokes_active_lease(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user="admin")
    assert modes.get_active_lease() is not None

    modes.set_kill_switch(True, reason="emergency")

    assert modes.get_active_lease() is None
    modes.set_kill_switch(False)
    # turning the switch back off does NOT resurrect the revoked lease
    assert modes.get_active_lease() is None


# --------------------------------------------- 6, 7, 8: no single setting

def test_env_flag_alone_cannot_authorize_an_order(monkeypatch):
    """binance_live_enabled=True with nothing else set: mode stays PAPER,
    so the client library must refuse to construct a write-capable client,
    and the gate must never even reach the lease check."""
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    client = HealthyClient()
    result = run_gate(modes.MODE_PAPER, client=client)
    assert not result.allowed
    assert not client.called


def test_db_live_mode_alone_cannot_authorize_an_order(monkeypatch):
    """mode=BINANCE_LIVE stored, but the env lock is off: effective_mode
    degrades to LOCKED and the gate refuses before any lease/exchange
    check - proves the DB alone is not a live-order side door."""
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    client = HealthyClient()
    result = asyncio.run(real_risk_gate.evaluate_real_order(
        client=client, symbol="BTCUSDT", side="LONG", notional_usdt=10.0, leverage=1.0, sl=45000.0, tp=55000.0,
    ))
    assert not result.allowed
    assert not client.called


def test_user_unlock_alone_cannot_authorize_an_order(monkeypatch):
    """The exact regression this phase fixes: env open, DB mode LIVE, and
    the legacy live_unlocked boolean True - but NO lease created. Before
    Phase 31 this combination alone was sufficient; now it must still be
    blocked. This is the real production configuration observed at the
    time of the 2026-07-15 incident."""
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    assert modes.get_control()["live_unlocked"] is True
    assert modes.effective_mode() == modes.MODE_LIVE  # the old gate would now be "open"

    client = HealthyClient()
    result = run_gate(modes.MODE_LIVE, client=client)
    assert not result.allowed
    assert "authorization lease" in result.reason.lower()
    assert not client.called


# ------------------------------------------- 9 & 10: mode-provider isolation

def test_paper_mode_never_resolves_to_the_live_provider():
    modes.set_mode(modes.MODE_PAPER)
    router = ExecutionRouter()
    provider = router.provider()
    assert isinstance(provider, PaperExecutionProvider)
    assert not isinstance(provider, BinanceExecutionProvider)


def test_testnet_mode_never_resolves_to_the_live_provider():
    modes.set_mode(modes.MODE_TESTNET)
    router = ExecutionRouter()
    provider = router.provider()
    assert isinstance(provider, BinanceExecutionProvider)
    assert provider.testnet is True
    assert provider is not router._live
    assert router._live is None  # never even lazily constructed


# --------------------------------------------------- 11: read-only keys

def test_read_only_client_cannot_submit_a_write(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    client = BinanceFuturesClient(testnet=False, read_only=True)
    with pytest.raises(LiveTradingLocked):
        asyncio.run(client.place_market_order("BTCUSDT", "BUY", 0.001))


def test_live_write_client_uses_separate_credentials_never_the_monitoring_pair(monkeypatch):
    """Phase 31 credential separation: a write-capable live client must
    never fall back to the shared read-only/testnet credential pair, even
    when that pair happens to be populated."""
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_api_key", "monitoring-key")
    monkeypatch.setattr(settings, "binance_api_secret", "monitoring-secret")
    monkeypatch.setattr(settings, "binance_live_api_key", "")
    monkeypatch.setattr(settings, "binance_live_api_secret", "")
    client = BinanceFuturesClient(testnet=False, read_only=False)
    assert client._api_key == ""
    assert client._api_secret == ""
    assert client.configured is False

    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    client2 = BinanceFuturesClient(testnet=False, read_only=False)
    assert client2._api_key == FAKE_LIVE_KEY
    assert client2._api_secret == FAKE_LIVE_SECRET

    # A read-only production client still uses the monitoring pair.
    read_client = BinanceFuturesClient(testnet=False, read_only=True)
    assert read_client._api_key == "monitoring-key"


# ---------------------------------------------- 12: inconsistent config fails closed

def test_inconsistent_env_and_db_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", False)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED
    assert modes.effective_mode() != modes.MODE_LIVE


# --------------------------------------------- 13: single-action consumption

def test_lease_is_consumed_after_one_order_and_blocks_the_next(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    monkeypatch.setattr(settings, "live_lease_max_actions", 1)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user="admin")

    first = run_gate(modes.MODE_LIVE)
    assert first.allowed
    consumed = modes.consume_active_lease()
    assert consumed["revoked"] is True

    real_risk_gate.reset_duplicate_guard()  # isolate from the duplicate-window check, not what this asserts
    second = run_gate(modes.MODE_LIVE)
    assert not second.allowed
    assert "authorization lease" in second.reason.lower()


def test_lease_scoped_to_one_symbol_blocks_a_different_symbol(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    modes.create_live_lease(user="admin", symbol_scope="ETHUSDT")

    result = run_gate(modes.MODE_LIVE, symbol="BTCUSDT")
    assert not result.allowed
    assert "scoped to ETHUSDT" in result.reason


# ------------------------------------------------- 15: never calls the client

def test_blocked_live_order_never_touches_the_exchange_client(monkeypatch):
    monkeypatch.setattr(settings, "binance_live_enabled", True)
    monkeypatch.setattr(settings, "binance_live_api_key", FAKE_LIVE_KEY)
    monkeypatch.setattr(settings, "binance_live_api_secret", FAKE_LIVE_SECRET)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    # deliberately no lease created
    client = HealthyClient()
    result = run_gate(modes.MODE_LIVE, client=client)
    assert not result.allowed
    assert client.called is False, "the gate must refuse before ever touching the exchange client"


# ------------------------------------------------------ 16: paper unaffected

def test_paper_trading_gate_is_entirely_unaffected_by_the_lease():
    """The lease mechanism is scoped to real orders only - paper trading
    (app/trading/risk_manager.py) never touches modes.get_active_lease at
    all, so paper trading works identically with or without a lease."""
    from app.trading import risk_manager
    decision = risk_manager.evaluate_risk(confidence=99.0, direction="LONG", open_positions=0)
    assert decision["allowed"] is True
