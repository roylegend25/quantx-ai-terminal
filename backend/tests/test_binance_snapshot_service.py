"""app/exchanges/binance_snapshot_service.py - the shared, single-flight,
stale-cache-preserving Binance data cache every portfolio/risk/watchdog
read goes through (Phase 29)."""

import asyncio

import pytest

from app.core.config import settings
from app.exchanges.binance_errors import BinanceRateLimitError
from app.exchanges.binance_models import BinanceAccountSummary, BinancePosition
from app.exchanges.binance_snapshot_service import BinanceSnapshotService


def account_summary(wallet=100.0):
    return BinanceAccountSummary(
        total_wallet_balance=wallet, available_balance=wallet, total_margin_balance=wallet,
        total_unrealized_pnl=0.0, total_initial_margin=0.0, total_maintenance_margin=0.0,
    )


def position(symbol="ETHUSDT"):
    return BinancePosition(
        symbol=symbol, side="LONG", quantity=1.0, entry_price=100.0, mark_price=101.0,
        leverage=2.0, margin_type="isolated", liquidation_price=50.0,
        unrealized_pnl=1.0, margin_used=50.0, notional=101.0,
    )


class CountingClient:
    """Records how many times each signed read is actually invoked, so
    coalescing/caching can be asserted directly instead of by timing."""

    configured = True
    read_only = True

    def __init__(self, wallet=100.0, positions=None, fail_after: int | None = None,
                error: Exception | None = None):
        self.calls = {"account": 0, "positions": 0, "daily_pnl": 0, "orders": 0, "income": 0, "balances": 0}
        self._wallet = wallet
        self._positions = positions or [position()]
        self._fail_after = fail_after
        self._error = error or BinanceRateLimitError("Too many requests", status=429)

    def _maybe_fail(self, key):
        if self._fail_after is not None and self.calls[key] > self._fail_after:
            raise self._error

    async def get_account_info(self):
        self.calls["account"] += 1
        self._maybe_fail("account")
        return account_summary(self._wallet)

    async def get_positions(self, symbol=None):
        self.calls["positions"] += 1
        self._maybe_fail("positions")
        return self._positions

    async def get_daily_realized_pnl(self):
        self.calls["daily_pnl"] += 1
        return 0.0

    async def get_open_orders(self, symbol=None):
        self.calls["orders"] += 1
        self._maybe_fail("orders")
        return []

    async def get_income_history(self, limit=50, income_type=None):
        self.calls["income"] += 1
        return [{"symbol": None, "income_type": "FUNDING_FEE", "income": -0.01, "asset": "USDT", "info": "", "time": 1}]

    async def get_balances(self):
        self.calls["balances"] += 1
        return []


@pytest.fixture
def service():
    return BinanceSnapshotService()


def test_account_bundle_is_cached_within_ttl(service):
    client = CountingClient()
    for _ in range(5):
        account, positions, daily_pnl = asyncio.run(service.get_account_bundle(client))
    assert client.calls["account"] == 1
    assert client.calls["positions"] == 1
    assert client.calls["daily_pnl"] == 1
    assert account.total_wallet_balance == 100.0
    assert positions[0].symbol == "ETHUSDT"


def test_five_concurrent_callers_produce_exactly_one_underlying_fetch(service):
    """The thundering-herd case this service exists to prevent: five
    "browser tabs" asking for the account bundle at the same instant must
    collapse to one signed Binance round trip, not five."""
    client = CountingClient()

    async def five_concurrent():
        await asyncio.gather(*[service.get_account_bundle(client) for _ in range(5)])

    asyncio.run(five_concurrent())
    assert client.calls["account"] == 1
    assert client.calls["positions"] == 1
    assert client.calls["daily_pnl"] == 1


def test_cache_invalidates_when_the_client_identity_changes(service):
    client_a = CountingClient()
    client_b = CountingClient(wallet=200.0)
    asyncio.run(service.get_account_bundle(client_a))
    account, _, _ = asyncio.run(service.get_account_bundle(client_b))
    assert account.total_wallet_balance == 200.0
    assert client_b.calls["account"] == 1


def test_force_refresh_bypasses_the_ttl_cache(service):
    client = CountingClient()
    asyncio.run(service.get_account_bundle(client))
    asyncio.run(service.get_account_bundle(client, force_refresh=True))
    assert client.calls["account"] == 2


def test_rate_limited_refresh_serves_last_known_good_positions_marked_stale(service, monkeypatch):
    monkeypatch.setattr(settings, "binance_enable_stale_cache_on_rate_limit", True)
    client = CountingClient(fail_after=1)  # first call succeeds, every call after fails

    account, positions, _ = asyncio.run(service.get_account_bundle(client))
    assert len(positions) == 1  # good data cached

    # Force a refresh that will hit the client's rate-limit failure - Phase
    # 29's core promise: this must NOT wipe the previously-good positions.
    account2, positions2, _ = asyncio.run(service.get_account_bundle(client, force_refresh=True))
    assert positions2 == positions  # same last-known-good list, never emptied
    assert service.section_meta()["account"]["stale"] is True
    assert service.section_meta()["account"]["error"]


def test_no_prior_good_data_still_raises_on_failure(service):
    """Only a section that has NEVER had good data should raise - there is
    genuinely nothing safe to fall back to yet, so failing loudly (not
    silently returning an empty list) is correct here."""
    client = CountingClient(fail_after=0)  # every call fails from the start

    with pytest.raises(BinanceRateLimitError):
        asyncio.run(service.get_account_bundle(client))


def test_stale_cache_can_be_disabled_via_settings(service, monkeypatch):
    monkeypatch.setattr(settings, "binance_enable_stale_cache_on_rate_limit", False)
    client = CountingClient(fail_after=1)
    asyncio.run(service.get_account_bundle(client))

    with pytest.raises(BinanceRateLimitError):
        asyncio.run(service.get_account_bundle(client, force_refresh=True))


def test_open_orders_and_income_are_cached_independently_of_account(service):
    client = CountingClient()
    asyncio.run(service.get_account_bundle(client))
    asyncio.run(service.get_open_orders(client))
    asyncio.run(service.get_income(client))
    assert client.calls["orders"] == 1
    assert client.calls["income"] == 1
    # calling account bundle again inside TTL must not touch orders/income
    asyncio.run(service.get_account_bundle(client))
    assert client.calls["orders"] == 1
    assert client.calls["income"] == 1


def test_reset_forgets_every_cached_section(service):
    client = CountingClient()
    asyncio.run(service.get_account_bundle(client))
    service.reset()
    asyncio.run(service.get_account_bundle(client))
    assert client.calls["account"] == 2
