import asyncio
import time

import pytest

from app.deployment.clock_preflight import evaluate_clock_preflight
from app.exchanges.binance_errors import BinanceTimestampError, BinanceTimestampUnsafe
from app.exchanges.binance_time import BinanceProduct, BinanceTimeService


def run(coro):
    return asyncio.run(coro)


def offset_fetcher(offsets, *, fail_calls=None, seconds_unit=False, delay=0.0):
    calls = {product: 0 for product in BinanceProduct}
    fail_calls = set(fail_calls or [])

    async def fetch(product):
        index = calls[product]
        calls[product] += 1
        if index in fail_calls:
            raise OSError("temporary time endpoint failure")
        if delay:
            await asyncio.sleep(delay)
        offset = offsets[product][min(index, len(offsets[product]) - 1)]
        value = int(time.time_ns() / 1_000_000 + offset)
        return {"serverTime": value // 1000 if seconds_unit else value}

    return fetch, calls


@pytest.mark.parametrize("offset", [425.0, -375.0])
def test_positive_and_negative_clock_offsets(offset):
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [offset] * 5})
    service = BinanceTimeService(fetcher=fetch)
    health = run(service.refresh(product, reason="test"))
    assert health["status"] == "synced"
    assert health["offset_ms"] == pytest.approx(offset, abs=4.0)


def test_seconds_milliseconds_mismatch_is_unsafe():
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [0] * 5}, seconds_unit=True)
    service = BinanceTimeService(fetcher=fetch)
    health = run(service.refresh(product, reason="unit-test"))
    assert health["status"] == "unsafe"
    assert health["valid_samples"] == 0


def test_stale_offset_degrades_then_becomes_unsafe():
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [0] * 5})
    service = BinanceTimeService(
        fetcher=fetch, refresh_interval_seconds=10, unsafe_after_seconds=20
    )
    assert run(service.refresh(product, reason="test"))["status"] == "synced"
    service.state(product).last_sync_monotonic = time.monotonic() - 15
    assert service.health(product)["status"] == "degraded"
    service.state(product).last_sync_monotonic = time.monotonic() - 25
    assert service.health(product)["status"] == "unsafe"


def test_high_latency_samples_are_rejected():
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [0] * 5}, delay=0.005)
    service = BinanceTimeService(fetcher=fetch, maximum_rtt_ms=1.0)
    health = run(service.refresh(product, reason="latency-test"))
    assert health["status"] == "unsafe"
    assert health["rejected_samples"] == 5


def test_one_failed_sample_still_produces_robust_sync():
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [120] * 5}, fail_calls={1})
    service = BinanceTimeService(fetcher=fetch)
    health = run(service.refresh(product, reason="one-failure"))
    assert health["status"] == "synced"
    assert health["valid_samples"] == 4
    assert health["rejected_samples"] == 1


def test_multiple_offset_outliers_do_not_move_median():
    product = BinanceProduct.USD_M_FUTURES
    fetch, _ = offset_fetcher({product: [100, 102, 101, 25_000, -25_000]})
    service = BinanceTimeService(fetcher=fetch)
    health = run(service.refresh(product, reason="outlier-test"))
    assert health["status"] == "synced"
    assert health["offset_ms"] == pytest.approx(101, abs=4.0)


def test_spot_and_futures_offsets_are_separate():
    offsets = {
        BinanceProduct.SPOT: [700] * 5,
        BinanceProduct.USD_M_FUTURES: [-600] * 5,
    }
    fetch, _ = offset_fetcher(offsets)
    service = BinanceTimeService(fetcher=fetch)
    spot = run(service.refresh(BinanceProduct.SPOT, reason="spot"))
    futures = run(service.refresh(BinanceProduct.USD_M_FUTURES, reason="futures"))
    assert spot["offset_ms"] == pytest.approx(700, abs=4)
    assert futures["offset_ms"] == pytest.approx(-600, abs=4)


def test_live_write_is_blocked_while_sync_is_unsafe(monkeypatch):
    import app.exchanges.binance_futures_client as module

    class UnsafeTime:
        async def ensure_synced(self, *args, **kwargs):
            raise BinanceTimestampUnsafe("unsafe clock")

    client = module.BinanceFuturesClient(api_key="x", api_secret="y", testnet=True)
    monkeypatch.setattr(module, "binance_time", UnsafeTime())
    with pytest.raises(BinanceTimestampUnsafe):
        run(client._post("/fapi/v1/order", {"symbol": "BTCUSDT"}))


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, responses, *args, **kwargs):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, *args, **kwargs):
        return self.responses.pop(0)


class HealthyFakeTime:
    def __init__(self):
        self.refreshes = 0

    async def ensure_synced(self, *args, **kwargs):
        return {"status": "synced"}

    async def refresh(self, *args, **kwargs):
        self.refreshes += 1
        return {"status": "synced"}

    def timestamp_ms(self, *args, **kwargs):
        return int(time.time() * 1000)

    def state(self, product):
        return type("State", (), {"offset_ms": 0.0})()


def test_timestamp_rejection_refreshes_once_then_succeeds(monkeypatch):
    import app.exchanges.binance_futures_client as module

    responses = [
        FakeResponse(400, {"code": -1021, "msg": "outside recvWindow"}),
        FakeResponse(200, []),
    ]
    fake_time = HealthyFakeTime()
    monkeypatch.setattr(module, "binance_time", fake_time)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(responses))
    client = module.BinanceFuturesClient(api_key="x", api_secret="y", testnet=True)
    assert run(client.get_positions()) == []
    assert fake_time.refreshes == 1
    assert responses == []


def test_timestamp_retry_is_bounded(monkeypatch):
    import app.exchanges.binance_futures_client as module

    responses = [
        FakeResponse(400, {"code": -1021, "msg": "outside recvWindow"}),
        FakeResponse(400, {"code": -1021, "msg": "outside recvWindow"}),
    ]
    fake_time = HealthyFakeTime()
    monkeypatch.setattr(module, "binance_time", fake_time)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(responses))
    client = module.BinanceFuturesClient(api_key="x", api_secret="y", testnet=True)
    with pytest.raises(BinanceTimestampError):
        run(client.get_positions())
    assert fake_time.refreshes == 1
    assert responses == []


def test_read_only_reconciliation_is_permitted_without_entry_permission(monkeypatch):
    import app.exchanges.binance_futures_client as module

    class DegradedReadTime(HealthyFakeTime):
        async def ensure_synced(self, *args, **kwargs):
            assert kwargs["require_safe"] is False
            return {"status": "degraded"}

    responses = [FakeResponse(200, [])]
    monkeypatch.setattr(module, "binance_time", DegradedReadTime())
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(responses))
    client = module.BinanceFuturesClient(
        api_key="x", api_secret="y", testnet=False, read_only=True
    )
    assert run(client.get_positions()) == []


@pytest.mark.parametrize(
    "ntp,host,container,binance_status,expected_reason",
    [
        (False, 1000, 1000, "synced", "NTP"),
        (True, 1000, 2501, "synced", "host and container"),
        (True, 1000, 1001, "unsafe", "Binance timestamp"),
    ],
)
def test_container_host_preflight_fails_closed(ntp, host, container, binance_status, expected_reason):
    result = evaluate_clock_preflight(
        ntp_synchronized=ntp, host_time_ms=host, container_time_ms=container,
        binance_time_status=binance_status,
    )
    assert result.ok is False
    assert expected_reason in result.reason
