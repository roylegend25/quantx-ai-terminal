"""Binance Algo Order API client (Phase 26 Algo Order Provider).

Places conditional TP/SL through Binance's dedicated Algo Order API
(POST /fapi/v1/algoOrder) instead of the classic order-entry endpoint
(POST /fapi/v1/order), for accounts where the classic endpoint rejects
STOP_MARKET/TAKE_PROFIT_MARKET with code -4120 STOP_ORDER_SWITCH_ALGO -
see app.trading.diagnostics for the investigation this is built on, and
app.trading.protection_provider for how/when this provider gets selected.

Every payload field below is taken verbatim from Binance's own docs
(developers.binance.com/docs/derivatives/usds-margined-futures/trade/
rest-api/{New,Cancel,Query}-Algo-Order), not guessed:

  POST   /fapi/v1/algoOrder  - algoType=CONDITIONAL, type=STOP_MARKET or
                                TAKE_PROFIT_MARKET, triggerPrice (not
                                stopPrice). Response includes algoId and
                                algoStatus.
  GET    /fapi/v1/algoOrder  - query one order by algoId or clientAlgoId.
  DELETE /fapi/v1/algoOrder  - cancel one order by algoId or clientAlgoId.

Binance also documents a "Current All Algo Open Orders" listing endpoint
(GET /fapi/v1/algoOpenOrders, symbol mandatory) - empirically tested live
against this account WITH that mandatory symbol parameter, it returns HTTP
404, not a result set. So verification here never depends on a listing
call: every algo order id returned by placement must be tracked by the
caller (see ExchangePositionRow.tp_algo_id / sl_algo_id) and re-checked
individually via GET by algoId, which IS confirmed reachable (an unknown
id returns a real Binance business error, -2013 "Order does not exist",
never a 404).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.exchanges.binance_errors import BinanceError
from app.exchanges.binance_futures_client import BinanceFuturesClient

# algoStatus values that mean "still resting / not yet triggered or
# canceled" - anything else (CANCELED, FILLED, EXPIRED, ...) means this
# algo order is no longer live protection.
ACTIVE_ALGO_STATUSES = {"NEW", "WORKING", "ACCEPTED"}

_SL_TYPES = ("STOP_MARKET", "STOP")
_TP_TYPES = ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")


@dataclass
class AlgoOrder:
    """Deliberately duck-type compatible with BinanceOrder (order_id, type,
    stop_price, reduce_only, close_position, symbol) so it can be merged
    directly into app.trading.protection.derive_protection's existing
    order list without that function needing to know algo orders exist."""

    order_id: int  # algoId
    client_order_id: str  # clientAlgoId
    symbol: str
    side: str
    position_side: str
    type: str  # orderType echoed back: STOP_MARKET | TAKE_PROFIT_MARKET | ...
    status: str  # algoStatus
    stop_price: float  # triggerPrice
    quantity: float
    reduce_only: bool
    close_position: bool
    raw: dict = field(default_factory=dict)

    @property
    def algo_id(self) -> int:
        return self.order_id

    @classmethod
    def from_api(cls, raw: dict) -> "AlgoOrder":
        # Binance's documented New/Query Algo Order response schema does
        # NOT echo back reduceOnly or closePosition (verified against the
        # response parameter table - only algoId, clientAlgoId, algoType,
        # orderType, symbol, side, positionSide, timeInForce, quantity,
        # algoStatus, triggerPrice, price, activatePrice, callbackRate,
        # create/update/triggerTime are documented). Falling back to
        # raw.get("reduceOnly") would silently read as False for every
        # real order and make derive_protection's reduce-only/close-
        # position filter exclude every algo order. Every AlgoOrder this
        # app ever constructs comes from an id THIS SYSTEM tracked as its
        # own protective TP/SL (see protection.resolve_protection - there
        # is no "list all algo orders" call that could pull in an
        # unrelated order), so it is correct, not an assumption, to treat
        # every one as reduce-only protection.
        reduce_only = bool(raw.get("reduceOnly")) or "reduceOnly" not in raw
        return cls(
            order_id=int(raw.get("algoId", 0)),
            client_order_id=raw.get("clientAlgoId", ""),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            position_side=raw.get("positionSide", "BOTH"),
            type=raw.get("orderType", ""),
            status=raw.get("algoStatus", ""),
            stop_price=float(raw.get("triggerPrice") or 0),
            quantity=float(raw.get("quantity") or 0),
            reduce_only=reduce_only,
            close_position=bool(raw.get("closePosition")),
            raw=raw,
        )

    def to_dict(self) -> dict:
        return {
            "algo_id": self.order_id, "client_algo_id": self.client_order_id, "symbol": self.symbol,
            "side": self.side, "position_side": self.position_side, "type": self.type,
            "status": self.status, "trigger_price": self.stop_price, "quantity": self.quantity,
        }


class BinanceAlgoProvider:
    """Protection provider backed by Binance's Algo Order API - same
    responsibilities as BinanceFuturesClient's classic place_stop_loss/
    place_take_profit, but a structurally separate order surface. The
    router (app.trading.protection_provider) picks exactly one provider
    per account/mode; this class never mixes with classic orders itself."""

    def __init__(self, client: BinanceFuturesClient):
        self.client = client

    async def _place(
        self, *, symbol: str, order_type: str, position_side: str, trigger_price: float,
        quantity: float | None, hedge_mode: bool, client_algo_id_prefix: str,
    ) -> AlgoOrder:
        symbol = symbol.upper()
        order_side = "SELL" if position_side.upper() == "LONG" else "BUY"
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": order_side,
            "type": order_type,
            "triggerPrice": trigger_price,
            "workingType": "MARK_PRICE",
            "clientAlgoId": self.client._client_order_id(client_algo_id_prefix)[:36],
        }
        if hedge_mode:
            params["positionSide"] = position_side.upper()
            if quantity:
                params["quantity"] = quantity
            else:
                params["closePosition"] = "true"
        else:
            if quantity:
                params["quantity"] = quantity
                params["reduceOnly"] = "true"
            else:
                params["closePosition"] = "true"
        response = await self.client._post("/fapi/v1/algoOrder", params)
        return AlgoOrder.from_api(response)

    async def place_take_profit(
        self, symbol: str, position_side: str, trigger_price: float,
        quantity: float | None = None, hedge_mode: bool = False,
    ) -> AlgoOrder:
        return await self._place(
            symbol=symbol, order_type="TAKE_PROFIT_MARKET", position_side=position_side,
            trigger_price=trigger_price, quantity=quantity, hedge_mode=hedge_mode,
            client_algo_id_prefix="qxatp",
        )

    async def place_stop_loss(
        self, symbol: str, position_side: str, trigger_price: float,
        quantity: float | None = None, hedge_mode: bool = False,
    ) -> AlgoOrder:
        return await self._place(
            symbol=symbol, order_type="STOP_MARKET", position_side=position_side,
            trigger_price=trigger_price, quantity=quantity, hedge_mode=hedge_mode,
            client_algo_id_prefix="qxasl",
        )

    async def cancel(self, algo_id: int | None = None, client_algo_id: str | None = None) -> dict:
        """Cancels by algoId when given; falls back to clientAlgoId - see
        Debug 1.pdf section 5. Never sends a classic orderId here."""
        if algo_id is None and not client_algo_id:
            raise ValueError("cancel requires algo_id or client_algo_id")
        params = {"algoId": int(algo_id)} if algo_id is not None else {"clientAlgoId": client_algo_id}
        return await self.client._delete("/fapi/v1/algoOrder", params)

    async def get(self, algo_id: int) -> AlgoOrder | None:
        """None if Binance reports the order doesn't exist (-2013) - that
        is itself a real, verified answer, not an error to propagate."""
        try:
            response = await self.client._get("/fapi/v1/algoOrder", {"algoId": int(algo_id)}, signed=True)
            return AlgoOrder.from_api(response)
        except BinanceError as e:
            if e.code == -2013:
                return None
            raise

    async def get_active(self, algo_id: int | None) -> AlgoOrder | None:
        """get() filtered to only a still-resting order - None for a
        missing, canceled, filled or expired one."""
        if not algo_id:
            return None
        order = await self.get(algo_id)
        return order if order and order.status in ACTIVE_ALGO_STATUSES else None
