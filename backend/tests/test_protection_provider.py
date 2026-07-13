"""Phase 26 Algo Order Provider: provider selection, auto-migration on
-4120, retry/backoff, cancel routing, and algo-aware protection
verification - app.trading.protection_provider, protection_capability,
app.exchanges.binance_algo_provider, and protection.resolve_protection."""

import asyncio

import pytest

from app.db.session import SessionLocal
from app.db.models import BinanceProtectionCapability
from app.exchanges.binance_errors import BinanceError
from app.trading import modes, protection, protection_capability, protection_provider
from tests.test_execution_router_binance import make_order


@pytest.fixture(autouse=True)
def clean_capability(monkeypatch):
    db = SessionLocal()
    try:
        db.query(BinanceProtectionCapability).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(protection_provider, "_BACKOFF_BASE_SECONDS", 0.001)
    yield


class AlgoMockClient:
    """Supports both the classic protective-order calls and the raw
    _post/_get/_delete surface BinanceAlgoProvider needs, so one mock
    exercises both providers."""

    testnet = True
    configured = True

    def __init__(self, classic_fails_with_4120=False, classic_transient_failures=0):
        self.calls: list[tuple] = []
        self._classic_fails = classic_fails_with_4120
        self._classic_transient_failures = classic_transient_failures
        self._next_order_id = 100
        self._next_algo_id = 5000
        self._algo_orders: dict[int, dict] = {}

    @staticmethod
    def _client_order_id(prefix="qx"):
        import uuid
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    def _classic_order(self, **kw):
        self._next_order_id += 1
        return make_order(order_id=self._next_order_id, **kw)

    async def _classic_leg(self, kind, symbol, position_side, stop_price, quantity, hedge_mode):
        self.calls.append((f"classic_{kind}", symbol, stop_price))
        if self._classic_fails:
            raise BinanceError(
                "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.",
                code=-4120, status=400,
            )
        if self._classic_transient_failures > 0:
            self._classic_transient_failures -= 1
            raise BinanceError("Timeout", code=-1021, status=418)
        order_type = "TAKE_PROFIT_MARKET" if kind == "tp" else "STOP_MARKET"
        return self._classic_order(symbol=symbol, type=order_type, stop_price=stop_price, reduce_only=True)

    async def place_take_profit(self, symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        return await self._classic_leg("tp", symbol, position_side, stop_price, quantity, hedge_mode)

    async def place_stop_loss(self, symbol, position_side, stop_price, quantity=None, client_order_id=None, hedge_mode=False):
        return await self._classic_leg("sl", symbol, position_side, stop_price, quantity, hedge_mode)

    async def cancel_order(self, symbol, order_id=None, orig_client_order_id=None):
        self.calls.append(("classic_cancel", symbol, order_id, orig_client_order_id))
        return make_order(order_id=order_id or 0, status="CANCELED")

    async def get_open_orders(self, symbol=None):
        return []

    async def _post(self, path, params=None):
        self.calls.append(("_post", path, params))
        if path == "/fapi/v1/algoOrder":
            self._next_algo_id += 1
            algo_id = self._next_algo_id
            self._algo_orders[algo_id] = {"status": "NEW", "params": params}
            return self._algo_response(algo_id)
        raise AssertionError(f"unexpected POST {path}")

    async def _get(self, path, params=None, signed=False):
        self.calls.append(("_get", path, params))
        if path == "/fapi/v1/algoOrder":
            algo_id = int(params["algoId"])
            order = self._algo_orders.get(algo_id)
            if not order:
                raise BinanceError("Order does not exist.", code=-2013, status=400)
            return self._algo_response(algo_id)
        raise AssertionError(f"unexpected GET {path}")

    async def _delete(self, path, params=None):
        self.calls.append(("_delete", path, params))
        if path == "/fapi/v1/algoOrder":
            algo_id = int(params["algoId"])
            if algo_id in self._algo_orders:
                self._algo_orders[algo_id]["status"] = "CANCELED"
            return {"algoId": algo_id, "clientAlgoId": "", "code": "200", "msg": "success"}
        raise AssertionError(f"unexpected DELETE {path}")

    def _algo_response(self, algo_id):
        order = self._algo_orders[algo_id]
        params = order["params"]
        return {
            "algoId": algo_id, "clientAlgoId": params.get("clientAlgoId", ""), "algoType": "CONDITIONAL",
            "orderType": params["type"], "symbol": params["symbol"], "side": params["side"],
            "positionSide": params.get("positionSide", "BOTH"), "algoStatus": order["status"],
            "triggerPrice": params.get("triggerPrice"), "quantity": params.get("quantity", 0),
        }

    def called(self, prefix):
        return [c for c in self.calls if c[0] == prefix]


MODE = modes.MODE_TESTNET


# --------------------------------------------------------- provider selection

def test_place_leg_uses_classic_when_it_succeeds():
    client = AlgoMockClient()
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="tp",
        price=1750.0, quantity=0.038,
    ))
    assert result.ok and result.provider == protection_provider.CLASSIC
    assert client.called("classic_tp")
    assert protection_capability.get_provider(MODE) == protection_provider.CLASSIC


def test_place_leg_migrates_to_algo_on_4120_and_persists():
    client = AlgoMockClient(classic_fails_with_4120=True)
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="tp",
        price=1750.0, quantity=0.038,
    ))
    assert result.ok and result.provider == protection_provider.ALGO and result.migrated
    assert client.called("classic_tp")  # the failed classic attempt still happened
    assert client.called("_post")  # then the algo attempt
    assert protection_capability.get_provider(MODE) == protection_provider.ALGO
    cap = protection_capability.snapshot(MODE)
    assert "4120" in cap["detection_reason"]


def test_second_order_after_migration_skips_classic_entirely():
    protection_capability.set_provider(MODE, protection_provider.ALGO, reason="pre-detected")
    client = AlgoMockClient(classic_fails_with_4120=True)  # would fail if classic were tried
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="sl",
        price=1850.0, quantity=0.038,
    ))
    assert result.ok and result.provider == protection_provider.ALGO
    assert not client.called("classic_sl")
    assert client.called("_post")


def test_place_protection_places_both_legs_and_reports_provider_used():
    client = AlgoMockClient()
    result = asyncio.run(protection_provider.place_protection(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT",
        tp=1750.0, sl=1850.0, quantity=0.038,
    ))
    assert result.ok
    assert result.provider_used == protection_provider.CLASSIC
    assert result.tp.ok and result.sl.ok


def test_place_protection_migrates_mid_call_so_second_leg_never_tries_classic():
    client = AlgoMockClient(classic_fails_with_4120=True)
    result = asyncio.run(protection_provider.place_protection(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT",
        tp=1750.0, sl=1850.0, quantity=0.038,
    ))
    assert result.ok
    assert result.provider_used == protection_provider.ALGO
    assert result.migrated
    # TP triggered the migration (classic attempted once); SL should have
    # gone straight to algo with no classic attempt at all.
    assert len(client.called("classic_tp")) == 1
    assert not client.called("classic_sl")


# --------------------------------------------------------------- retry policy

def test_transient_classic_failure_is_retried_and_recovers():
    client = AlgoMockClient(classic_transient_failures=1)
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="tp",
        price=1750.0, quantity=0.038,
    ))
    assert result.ok
    assert result.attempts == 2


def test_exhausted_retries_report_failure_not_a_raise():
    client = AlgoMockClient(classic_transient_failures=99)
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="tp",
        price=1750.0, quantity=0.038,
    ))
    assert not result.ok
    assert result.attempts == protection_provider.MAX_RETRIES + 1
    assert result.error


def test_no_price_short_circuits_without_calling_binance():
    client = AlgoMockClient()
    result = asyncio.run(protection_provider.place_leg(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT", kind="tp",
        price=None, quantity=0.038,
    ))
    assert not result.ok
    assert not client.calls


# ------------------------------------------------------------- cancel routing

def test_cancel_leg_routes_algo_to_delete_algo_order():
    client = AlgoMockClient()
    asyncio.run(protection_provider.cancel_leg(client, "ETHUSDT", 5001, protection_provider.ALGO))
    assert client.called("_delete")[0][2] == {"algoId": 5001}


def test_cancel_leg_routes_classic_to_cancel_order():
    client = AlgoMockClient()
    asyncio.run(protection_provider.cancel_leg(client, "ETHUSDT", 101, protection_provider.CLASSIC))
    assert client.called("classic_cancel")[0] == ("classic_cancel", "ETHUSDT", 101, None)


# -------------------------------------------------------- capability module

def test_capability_defaults_to_none_until_detected():
    assert protection_capability.get_provider(MODE) is None


def test_capability_reset_forgets_detection():
    protection_capability.set_provider(MODE, protection_provider.ALGO, reason="test")
    assert protection_capability.get_provider(MODE) == protection_provider.ALGO
    protection_capability.reset(MODE)
    assert protection_capability.get_provider(MODE) is None


def test_capability_is_per_mode_not_shared():
    protection_capability.set_provider(modes.MODE_TESTNET, protection_provider.ALGO)
    protection_capability.set_provider(modes.MODE_LIVE, protection_provider.CLASSIC)
    assert protection_capability.get_provider(modes.MODE_TESTNET) == protection_provider.ALGO
    assert protection_capability.get_provider(modes.MODE_LIVE) == protection_provider.CLASSIC


# --------------------------------------------------- algo-aware verification

def test_resolve_protection_merges_active_algo_orders():
    client = AlgoMockClient(classic_fails_with_4120=True)
    result = asyncio.run(protection_provider.place_protection(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT",
        tp=1750.0, sl=1850.0, quantity=0.038,
    ))
    assert result.provider_used == protection_provider.ALGO
    verified = asyncio.run(protection.resolve_protection(
        client, "ETHUSDT", open_orders=[], tp_algo_id=result.tp.order_id, sl_algo_id=result.sl.order_id,
    ))
    assert verified.status == protection.PROTECTED
    assert verified.tp_order_id == result.tp.order_id
    assert verified.sl_order_id == result.sl.order_id


def test_resolve_protection_reports_missing_when_algo_order_canceled():
    client = AlgoMockClient(classic_fails_with_4120=True)
    result = asyncio.run(protection_provider.place_protection(
        client=client, mode=MODE, symbol="ETHUSDT", position_side="SHORT",
        tp=1750.0, sl=1850.0, quantity=0.038,
    ))
    assert result.provider_used == protection_provider.ALGO
    asyncio.run(protection_provider.cancel_leg(client, "ETHUSDT", result.sl.order_id, protection_provider.ALGO))

    verified = asyncio.run(protection.resolve_protection(
        client, "ETHUSDT", open_orders=[], tp_algo_id=result.tp.order_id, sl_algo_id=result.sl.order_id,
    ))
    assert verified.status == protection.MISSING_SL


def test_resolve_protection_handles_unknown_algo_id_gracefully():
    client = AlgoMockClient()
    verified = asyncio.run(protection.resolve_protection(
        client, "ETHUSDT", open_orders=[], tp_algo_id=999999, sl_algo_id=None,
    ))
    assert verified.status == protection.MISSING_BOTH
