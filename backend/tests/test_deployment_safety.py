import asyncio

from app.core.config import settings
from app.deployment import maintenance
from app.deployment.lease import ExecutionLease
from app.trading.execution_router import ExecutionRouter


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
