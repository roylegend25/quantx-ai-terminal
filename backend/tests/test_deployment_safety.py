import asyncio

from app.core.config import settings
from app.deployment import maintenance
from app.deployment.lease import ExecutionLease
from app.trading.execution_router import ExecutionRouter
from app.trading import scheduler


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, script, _keys, key, owner, *args):
        if self.values.get(key) != owner:
            return 0
        if "del" in script:
            del self.values[key]
        return 1


def test_deployment_maintenance_blocks_before_provider(monkeypatch, tmp_path):
    marker = tmp_path / "maintenance"
    monkeypatch.setattr(settings, "deployment_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "deployment_maintenance_mode", True)
    router = ExecutionRouter()

    result = asyncio.run(
        router.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=10)
    )

    assert result.ok is False
    assert result.reason == "DEPLOYMENT_MAINTENANCE"


def test_maintenance_marker_survives_runtime_flag_change(monkeypatch, tmp_path):
    marker = tmp_path / "maintenance"
    monkeypatch.setattr(settings, "deployment_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "deployment_maintenance_mode", False)
    maintenance.enable("test")
    monkeypatch.setattr(settings, "deployment_maintenance_mode", False)
    assert maintenance.enabled() is True


def test_only_one_execution_lease_owner(monkeypatch):
    store = FakeRedis()
    first = ExecutionLease()
    second = ExecutionLease()
    monkeypatch.setattr(first, "_redis", lambda: store)
    monkeypatch.setattr(second, "_redis", lambda: store)

    assert asyncio.run(first.acquire()) is True
    assert asyncio.run(second.acquire()) is False
    assert asyncio.run(first.owns()) is True
    assert asyncio.run(second.owns()) is False


def test_lease_loss_is_detected(monkeypatch):
    store = FakeRedis()
    lease = ExecutionLease()
    monkeypatch.setattr(lease, "_redis", lambda: store)
    assert asyncio.run(lease.acquire()) is True
    store.values[lease.key] = "replacement-owner"
    assert asyncio.run(lease.owns()) is False
    assert lease.held is False


def test_expired_lease_is_reacquired_without_bypassing_competing_owner(monkeypatch):
    store = FakeRedis()
    lease = ExecutionLease()
    monkeypatch.setattr(lease, "_redis", lambda: store)
    assert asyncio.run(lease.acquire_or_renew()) is True

    del store.values[lease.key]  # Redis TTL elapsed between scheduler cycles.
    assert asyncio.run(lease.acquire_or_renew()) is True
    assert store.values[lease.key] == lease.owner

    store.values[lease.key] = "replacement-owner"
    assert asyncio.run(lease.acquire_or_renew()) is False
    assert store.values[lease.key] == "replacement-owner"


def test_scheduler_renews_lease_during_long_cycle(monkeypatch):
    stop = asyncio.Event()
    renewals = []

    async def renew():
        renewals.append(True)
        stop.set()
        return True

    async def timeout_immediately(awaitable, *, timeout):
        del timeout
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(scheduler.execution_lease, "renew", renew)
    monkeypatch.setattr(scheduler.asyncio, "wait_for", timeout_immediately)

    asyncio.run(scheduler._renew_execution_lease(stop))

    assert renewals == [True]


def test_scheduler_refreshes_stale_timestamp_before_live_cycle(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler.maintenance, "enabled", lambda: False)
    monkeypatch.setattr(
        scheduler.modes,
        "get_control",
        lambda: {"execution_enabled": True, "execution_state": "running"},
    )
    monkeypatch.setattr(scheduler.modes, "effective_mode", lambda: scheduler.modes.MODE_LIVE)
    monkeypatch.setattr(
        scheduler.binance_time,
        "health",
        lambda _product: {"status": "degraded", "sample_age_seconds": 301.0},
    )

    async def refresh(_product, *, reason):
        calls.append(reason)
        return {"status": "synced", "sample_age_seconds": 0.0}

    async def run_cycle():
        calls.append("cycle")

    async def stop_after_iteration(_seconds):
        scheduler.RUNNING = False

    monkeypatch.setattr(scheduler.binance_time, "refresh", refresh)
    monkeypatch.setattr(scheduler, "_run_engine_cycle", run_cycle)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_iteration)
    scheduler.RUNNING = True

    asyncio.run(scheduler.trading_loop())

    assert calls == ["scheduler_cycle_preflight", "cycle"]
