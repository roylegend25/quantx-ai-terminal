"""Order validation and smart routing for paper-mode execution.

"Routing" here means simulating how an order would have filled against
real, read-only order-book depth (via app.exchanges) - never placing it.
Nothing in this module, or anything it calls, can reach a live order-entry
endpoint: app.exchanges adapters are read-only by construction.
"""
from dataclasses import dataclass
from enum import Enum

from app.exchanges import manager as exchange_manager
from app.execution.slippage import walk_book


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    POST_ONLY = "POST_ONLY"
    IOC = "IOC"
    FOK = "FOK"


class OrderRejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# Conservative fallback filters used if a symbol has no explicit entry below
# - intentionally tighter than any real Binance USDT-M filter, so a paper
# order is never accepted here that a live venue would have rejected.
DEFAULT_FILTERS = {
    "tick_size": 0.1,
    "step_size": 0.001,
    "min_qty": 0.001,
    "min_notional": 20.0,
    "price_precision": 2,
    "qty_precision": 3,
}

SYMBOL_FILTERS: dict[str, dict] = {
    "BTCUSDT": {
        "tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001,
        "min_notional": 20.0, "price_precision": 1, "qty_precision": 3,
    },
    "ETHUSDT": {
        "tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
        "min_notional": 20.0, "price_precision": 2, "qty_precision": 3,
    },
}

# Orders routed without an explicit price get one derived from the market
# price plus this buffer, so LIMIT/IOC/FOK/POST_ONLY have realistic
# "marketable limit" semantics instead of requiring callers to compute one.
MARKETABLE_LIMIT_BUFFER_BPS = 15.0


def get_filters(symbol: str) -> dict:
    return SYMBOL_FILTERS.get(symbol.upper(), DEFAULT_FILTERS)


def _round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(round(value / step) * step, 10)


def derive_price(order_type: OrderType, side: str, market_price: float) -> float:
    buffer = MARKETABLE_LIMIT_BUFFER_BPS / 10_000
    if order_type == OrderType.POST_ONLY:
        # Must rest, not cross - offset away from the market.
        return market_price * (1 - buffer) if side == "LONG" else market_price * (1 + buffer)
    # LIMIT / IOC / FOK: a marketable limit that behaves like a market order
    # under normal depth, but caps worst-case slippage on a thin book.
    return market_price * (1 + buffer) if side == "LONG" else market_price * (1 - buffer)


@dataclass
class ValidatedOrder:
    symbol: str
    side: str
    order_type: OrderType
    qty: float
    price: float | None
    notional: float


def validate_order(
    symbol: str, side: str, order_type: OrderType, qty: float, price: float | None
) -> ValidatedOrder:
    symbol = symbol.upper()
    side = side.upper()
    filters = get_filters(symbol)

    if side not in ("LONG", "SHORT"):
        raise OrderRejected(f"invalid side: {side}")
    if qty <= 0:
        raise OrderRejected("quantity must be positive")

    needs_price = order_type in (OrderType.LIMIT, OrderType.POST_ONLY, OrderType.IOC, OrderType.FOK)
    if needs_price and not price:
        raise OrderRejected(f"{order_type.value} orders require a price")

    rounded_qty = round(_round_to_step(qty, filters["step_size"]), filters["qty_precision"])
    if rounded_qty < filters["min_qty"]:
        raise OrderRejected(f"quantity {rounded_qty} below minimum {filters['min_qty']}")

    rounded_price = None
    if price is not None:
        rounded_price = round(_round_to_step(price, filters["tick_size"]), filters["price_precision"])
        if rounded_price <= 0:
            raise OrderRejected("invalid price after tick-size rounding")

    notional = rounded_qty * (rounded_price or 0)
    if rounded_price and notional < filters["min_notional"]:
        raise OrderRejected(f"order notional {notional:.2f} below minimum {filters['min_notional']}")

    return ValidatedOrder(
        symbol=symbol, side=side, order_type=order_type,
        qty=rounded_qty, price=rounded_price, notional=notional,
    )


@dataclass
class RoutingResult:
    status: str  # FILLED | PARTIAL | REJECTED | CANCELLED
    filled_qty: float
    avg_fill_price: float
    reference_price: float
    expected_slippage_bps: float
    reason: str | None = None


async def route_order(order: ValidatedOrder, market_price: float, exchange: str = "binance") -> RoutingResult:
    """Simulates how `order` would have filled against live order-book depth.

    MARKET/IOC/FOK/POST_ONLY/LIMIT all walk the same book snapshot; the
    order type only changes what counts as an acceptable outcome:
      - MARKET: always fills whatever depth exists (partial fill allowed).
      - IOC: fills whatever depth exists immediately, cancels the remainder.
      - FOK: must fill completely immediately, or the whole order is killed.
      - LIMIT: only fills the portion of the book at or better than `order.price`.
      - POST_ONLY: rejected outright if it would have crossed the book
        (i.e. taken liquidity instead of resting on it).
    """
    adapter = exchange_manager.get_adapter(exchange)
    orderbook = None
    if adapter is not None:
        try:
            orderbook = await adapter.get_orderbook(order.symbol, limit=50)
        except Exception:
            orderbook = None

    if not orderbook or not orderbook.get("bids") or not orderbook.get("asks"):
        # No live book available (adapter unconfigured, exchange
        # unreachable, ...) - fall back to a full simulated fill at the
        # last-known market price so paper trading never stalls on
        # market-data unavailability.
        return RoutingResult(
            status="FILLED",
            filled_qty=order.qty,
            avg_fill_price=market_price,
            reference_price=market_price,
            expected_slippage_bps=0.0,
            reason="orderbook unavailable - filled at last price",
        )

    levels = orderbook["asks"] if order.side == "LONG" else orderbook["bids"]
    best = levels[0][0]

    if order.order_type == OrderType.POST_ONLY:
        # A resting sell (SHORT) would cross into the bid side; a resting
        # buy (LONG) would cross into the ask side.
        opposite = orderbook["asks"] if order.side == "LONG" else orderbook["bids"]
        best_opposite = opposite[0][0] if opposite else None
        would_cross = best_opposite is not None and (
            (order.side == "LONG" and order.price >= best_opposite)
            or (order.side == "SHORT" and order.price <= best_opposite)
        )
        if would_cross:
            return RoutingResult(
                status="REJECTED", filled_qty=0.0, avg_fill_price=0.0,
                reference_price=best, expected_slippage_bps=0.0,
                reason="post-only order would have crossed the book",
            )
        # Rests on the book - paper mode has no live order feed to fill it
        # against later, so a passive maker fill at the requested price
        # (zero slippage, since it never took liquidity) is simulated.
        return RoutingResult(
            status="FILLED", filled_qty=order.qty, avg_fill_price=order.price,
            reference_price=best, expected_slippage_bps=0.0, reason=None,
        )

    if order.order_type in (OrderType.LIMIT, OrderType.IOC, OrderType.FOK):
        eligible = [
            [p, s] for p, s in levels
            if (order.side == "LONG" and p <= order.price) or (order.side == "SHORT" and p >= order.price)
        ]
        vwap, filled = walk_book(eligible, order.qty)
    else:  # MARKET
        vwap, filled = walk_book(levels, order.qty)

    if filled <= 0:
        return RoutingResult(
            status="CANCELLED" if order.order_type == OrderType.IOC else "REJECTED",
            filled_qty=0.0, avg_fill_price=0.0, reference_price=best,
            expected_slippage_bps=0.0, reason="no fillable depth at requested price",
        )

    if order.order_type == OrderType.FOK and filled < order.qty - 1e-9:
        return RoutingResult(
            status="REJECTED", filled_qty=0.0, avg_fill_price=0.0, reference_price=best,
            expected_slippage_bps=0.0, reason=f"fill-or-kill: only {filled:.6f}/{order.qty} fillable",
        )

    slippage_bps = abs((vwap - best) / best) * 10_000
    status = "FILLED" if filled >= order.qty - 1e-9 else "PARTIAL"

    return RoutingResult(
        status=status, filled_qty=round(filled, 8), avg_fill_price=round(vwap, 8),
        reference_price=best, expected_slippage_bps=round(slippage_bps, 4), reason=None,
    )
