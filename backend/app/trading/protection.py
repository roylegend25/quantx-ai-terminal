"""TP/SL protection status for real Binance positions (Phase 27).

Single source of truth, shared by the risk gate, the /protect repair
endpoint, the watchdog and the positions API: protection status is ALWAYS
derived from Binance's own open orders, never from local DB state. A local
row claiming a position has TP/SL is worthless if the exchange doesn't
agree - see PROTECTION_STALE_LOCAL below.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Phase 29 (section 11): algo orders never appear in get_open_orders, so
# resolve_protection looks up each tracked algo id individually - the
# positions endpoint, the watchdog scan and every risk/portfolio card that
# calls resolve_protection would otherwise re-query the SAME algoId several
# times a minute. Cached process-wide, keyed by algo id, for a few seconds -
# an active algo order's status/price don't change on their own between
# ticks, so a short TTL costs nothing in freshness but removes the repeat
# Binance calls.
_algo_lookup_cache: dict[int, tuple[float, object]] = {}


def _algo_ttl() -> float:
    from app.core.config import settings
    return settings.binance_algo_ttl_seconds


async def _get_active_algo_cached(algo, algo_id: int | None):
    """Phase 30: a failed lookup (rate limit, network blip, cooldown) is
    NOT the same fact as "Binance confirms this order is gone" - only
    BinanceAlgoProvider.get() returning None (a genuine -2013 "does not
    exist") means that. Root cause of a real production flap this fixes:
    a transient 429 on this exact call used to be swallowed by
    resolve_protection's caller as "order not active", instantly turning a
    position that was PROTECTED moments earlier into MISSING_TP_SL with no
    actual change on Binance. Serving the last successfully confirmed
    answer instead - stale, but never a false negative - is what section 6
    ("never downgrade to missing merely because a refresh failed") means
    applied at the individual algo-order level, not just the account
    snapshot level."""
    if not algo_id:
        return None
    cached = _algo_lookup_cache.get(algo_id)
    now = time.time()
    if cached and now - cached[0] < _algo_ttl():
        return cached[1]
    try:
        order = await algo.get_active(algo_id)
    except Exception:
        if cached:
            return cached[1]  # last confirmed answer - stale, not a false "gone"
        return None  # never successfully resolved this id - genuinely unknown
    _algo_lookup_cache[algo_id] = (now, order)
    return order


def reset_algo_lookup_cache() -> None:
    """Test hook + call after any placement/cancel of an algo order so the
    very next verification never reads a just-invalidated stale entry."""
    _algo_lookup_cache.clear()


PROTECTED = "PROTECTED"
MISSING_TP = "MISSING_TP"
MISSING_SL = "MISSING_SL"
MISSING_BOTH = "MISSING_TP_SL"
UNKNOWN = "UNKNOWN"

_SL_TYPES = ("STOP_MARKET", "STOP")
_TP_TYPES = ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")


@dataclass
class ProtectionStatus:
    status: str
    sl_price: float | None
    sl_order_id: int | None
    tp_price: float | None
    tp_order_id: int | None
    local_stale: bool  # local DB claims protection that Binance doesn't show

    def to_dict(self) -> dict:
        return {
            "protection_status": self.status,
            "live_take_profit": self.tp_price,
            "live_stop_loss": self.sl_price,
            "tp_order_id": self.tp_order_id,
            "sl_order_id": self.sl_order_id,
            "local_stale": self.local_stale,
        }


def derive_protection(
    symbol: str, open_orders: list, local_sl: float | None = None, local_tp: float | None = None,
) -> ProtectionStatus:
    """Reduce a symbol's open orders (any shape with .symbol/.type/.side/
    .stop_price/.order_id/.reduce_only/.close_position - a real BinanceOrder
    or an equivalent test double) down to its protection verdict. Only
    reduce-only or closePosition conditional exit orders count - a plain
    resting limit order on the same symbol is not protection."""
    symbol = symbol.upper()
    matching = [
        o for o in open_orders
        if o.symbol.upper() == symbol and (getattr(o, "reduce_only", False) or getattr(o, "close_position", False))
    ]
    sl_order = next((o for o in matching if o.type in _SL_TYPES), None)
    tp_order = next((o for o in matching if o.type in _TP_TYPES), None)

    has_sl = sl_order is not None
    has_tp = tp_order is not None
    if has_sl and has_tp:
        status = PROTECTED
    elif has_sl:
        status = MISSING_TP
    elif has_tp:
        status = MISSING_SL
    else:
        status = MISSING_BOTH

    local_stale = (local_sl is not None and not has_sl) or (local_tp is not None and not has_tp)

    return ProtectionStatus(
        status=status,
        sl_price=sl_order.stop_price if sl_order else None,
        sl_order_id=sl_order.order_id if sl_order else None,
        tp_price=tp_order.stop_price if tp_order else None,
        tp_order_id=tp_order.order_id if tp_order else None,
        local_stale=local_stale,
    )


def is_unprotected(status: str) -> bool:
    return status in (MISSING_TP, MISSING_SL, MISSING_BOTH)


async def resolve_protection(
    client, symbol: str, *, open_orders: list | None = None,
    tp_algo_id: int | None = None, sl_algo_id: int | None = None,
    local_sl: float | None = None, local_tp: float | None = None,
) -> ProtectionStatus:
    """Ground-truth protection check covering BOTH order surfaces (Phase 26
    Algo Order Provider): Binance's classic GET /fapi/v1/openOrders never
    returns Algo Order API orders, and no reliable "list open algo orders"
    endpoint exists for this account (see
    app.exchanges.binance_algo_provider), so a tracked algo id is looked up
    individually and merged into the same reduce-only/closePosition scan
    derive_protection already does for classic orders - callers never need
    to know which surface is active."""
    symbol = symbol.upper()
    if open_orders is None:
        open_orders = await client.get_open_orders(symbol)

    algo_orders = []
    if tp_algo_id or sl_algo_id:
        from app.exchanges.binance_algo_provider import BinanceAlgoProvider

        algo = BinanceAlgoProvider(client)
        for algo_id in filter(None, [tp_algo_id, sl_algo_id]):
            order = await _get_active_algo_cached(algo, algo_id)  # never raises - see its docstring
            if order:
                algo_orders.append(order)

    return derive_protection(symbol, open_orders + algo_orders, local_sl=local_sl, local_tp=local_tp)


async def check_all_positions_protected(
    client, *, get_tracked_algo_ids=lambda symbol: (None, None),
) -> tuple[bool, list[dict]]:
    """Ground-truth answer to "is every real open position protected right
    now" - the ONE shared implementation behind both the real risk gate's
    existing_position_protected check (app.trading.real_risk_gate) and any
    preview/diagnostic view of the same rule (Bot Decision Status,
    Execution Pipeline's current-protection-check). These must never
    compute this independently, or they can silently disagree - which is
    exactly the Phase 30 bug this function fixes: the risk gate used to
    call derive_protection() directly (classic orders only), so an
    Algo-protected position looked unprotected there even though every
    other consumer of protection status already used resolve_protection()
    and correctly reported PROTECTED.

    `get_tracked_algo_ids(symbol)` supplies each position's tracked Algo
    TP/SL ids - this module never touches the database itself, callers
    inject that lookup (see app.trading.execution_router._tracked_algo_id
    for the canonical implementation).

    Reads positions/open orders through the shared snapshot cache (Phase
    29/30) rather than calling the client directly - this function is now
    called by every poll of the Bot Decision Status / Execution Pipeline
    previews in addition to the real risk gate, so an uncached direct call
    here would reintroduce exactly the duplicate-Binance-traffic problem
    those phases fixed (confirmed live: this was the actual proximate
    cause of a real 429 during verification of this very fix)."""
    from app.exchanges.binance_snapshot_service import snapshot_service

    _, live_positions, _ = await snapshot_service.get_account_bundle(client)
    live_orders = await snapshot_service.get_open_orders(client)
    unprotected: list[dict] = []
    for pos in live_positions:
        tp_algo_id, sl_algo_id = get_tracked_algo_ids(pos.symbol)
        result = await resolve_protection(
            client, pos.symbol, open_orders=live_orders, tp_algo_id=tp_algo_id, sl_algo_id=sl_algo_id,
        )
        if is_unprotected(result.status):
            unprotected.append({"symbol": pos.symbol, "status": result.status})
    return not unprotected, unprotected
