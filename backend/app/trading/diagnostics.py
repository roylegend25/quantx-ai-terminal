"""Trading Diagnostics (Phase 28).

Built to investigate one specific, evidenced finding: this account's
STOP_MARKET/TAKE_PROFIT_MARKET orders are rejected by POST /fapi/v1/order
with Binance error -4120 ("Order type not supported for this endpoint.
Please use the Algo Order API endpoints instead"), confirmed by two real
attempts against the live account (see BinanceExecutionAttempt history).

Binance's own developer documentation (developers.binance.com/docs/
derivatives/usds-margined-futures) independently confirms:
  - Error -4120 is named STOP_ORDER_SWITCH_ALGO, with exactly this message.
  - A separate, fully-specified endpoint exists for conditional orders:
    POST /fapi/v1/algoOrder ("Send in a new algo (conditional) order. Use
    this endpoint to place TP/SL ... on USD-M Futures"), requiring
    algoType=CONDITIONAL and using `triggerPrice` (not `stopPrice`) as the
    trigger field - a materially different payload shape than the classic
    endpoint, not just a different URL.

This module surfaces every fact needed to confirm or refute that
conclusion for THIS account, gathered live wherever possible and marked
"unavailable" (never fabricated) when Binance can't be reached (e.g. rate
limiting). Nothing here ever places a real order - the one call that talks
to Binance's order-entry surface (test_stop_market_order) uses Binance's
own documented no-op /fapi/v1/order/test validation endpoint.

Also confirmed live: GET /fapi/v1/algoOrder (singular, no algoId) returns
a genuine Binance parameter-validation error (-1102), not a 404 - direct
proof this account/key can reach the Algo Order API surface. Three guessed
"list open algo orders" paths (algoOpenOrders, algo/futures/openOrders,
algo/futures/historicalOrders) all 404'd; no confirmed listing endpoint
has been found yet, so probe_algo_api_reachable() only demonstrates base
reachability, not order visibility.
"""

from __future__ import annotations

from app.core.config import settings
from app.exchanges.binance_futures_client import BinanceFuturesClient


async def _safe(coro):
    """Run one diagnostic sub-call; degrade to {"available": False,
    "reason": ...} instead of failing the whole diagnostics response."""
    try:
        return {"available": True, "value": await coro}
    except Exception as e:
        from app.trading.execution_router import _safe_error
        return {"available": False, "reason": _safe_error(e)}


async def build_account_diagnostics(client: BinanceFuturesClient) -> dict:
    """Step 1: account type, mode and permission facts - every value read
    live from Binance, or explicitly marked unavailable."""
    account = await _safe(client._get("/fapi/v2/account", signed=True))
    position_mode = await _safe(client._get("/fapi/v1/positionSide/dual", signed=True))
    multi_assets = await _safe(client.get_multi_assets_margin())
    api_permissions = await _safe(client.get_api_key_permissions())

    positions = await _safe(client.get_positions())
    margin_modes = None
    if positions["available"]:
        margin_modes = [{"symbol": p.symbol, "margin_type": p.margin_type} for p in positions["value"]]

    account_fields = None
    if account["available"]:
        a = account["value"]
        account_fields = {
            "fee_tier": a.get("feeTier"),
            "can_trade": a.get("canTrade"),
            "can_deposit": a.get("canDeposit"),
            "can_withdraw": a.get("canWithdraw"),
            "trade_group_id": a.get("tradeGroupId"),
            # tradeGroupId == -1 means not part of any broker/VIP program -
            # a genuine signal (not a guess) that this is a standard retail
            # account, reported alongside its raw value so the caller can
            # judge for themselves rather than trust our interpretation.
            "trade_group_id_interpretation": (
                "-1 = standard retail account, not a broker sub-account" if a.get("tradeGroupId") == -1
                else "non -1 tradeGroupId - possible broker/managed account, verify with Binance support"
            ),
        }

    return {
        "market": "USDS-M Futures" if "fapi.binance.com" in client.base_url or "testnet.binancefuture.com" in client.base_url else "unknown",
        "base_url": client.base_url,
        "environment": "TESTNET" if client.testnet else "PRODUCTION",
        "account_fields": account_fields,
        "account_fields_error": account.get("reason"),
        "position_mode": (
            "HEDGE" if position_mode["available"] and position_mode["value"].get("dualSidePosition")
            else "ONE-WAY" if position_mode["available"] else None
        ),
        "position_mode_error": position_mode.get("reason"),
        "multi_assets_margin": multi_assets.get("value"),
        "multi_assets_margin_error": multi_assets.get("reason"),
        "margin_mode_per_position": margin_modes,
        "margin_mode_error": positions.get("reason"),
        "api_permissions": api_permissions.get("value"),
        "api_permissions_error": api_permissions.get("reason"),
        # Portfolio Margin accounts use an entirely different API surface
        # (papi.binance.com) - the classic /fapi/v2/account call succeeding
        # with ordinary single-asset-margin-style fields is real (if
        # indirect) evidence against Portfolio Margin, not proof; a
        # dedicated GET /papi/v1/account probe would be the direct test.
        "portfolio_margin_indirect_evidence": (
            "Classic /fapi/v2/account responded normally with standard fields - indirect evidence this is NOT "
            "a Portfolio Margin account (PM accounts use a separate papi.binance.com surface). Not a direct test."
            if account["available"] else None
        ),
    }


async def build_order_type_diagnostics(client: BinanceFuturesClient, symbol: str) -> dict:
    """Steps 3-5: what /fapi/v1/order claims to support (exchangeInfo) vs.
    what the dedicated Algo Order API is for, vs. whether this account can
    actually reach the Algo Order API at all."""
    filters = await _safe(client._get("/fapi/v1/exchangeInfo"))
    order_types = None
    if filters["available"]:
        info = next((s for s in filters["value"].get("symbols", []) if s.get("symbol") == symbol.upper()), None)
        order_types = (info or {}).get("orderTypes")

    algo_probe = await _safe(client.probe_algo_api_reachable())

    return {
        "symbol": symbol.upper(),
        "order_types_per_exchange_info": order_types,
        "order_types_error": filters.get("reason"),
        "entry_endpoint_used_by_this_app": "POST /fapi/v1/order (type=MARKET) - confirmed working",
        "protective_endpoint_used_by_this_app": "POST /fapi/v1/order (type=STOP_MARKET / TAKE_PROFIT_MARKET) - confirmed REJECTED, code -4120",
        "documented_algo_endpoint": "POST /fapi/v1/algoOrder (algoType=CONDITIONAL) - per developers.binance.com, not yet used by this app",
        # Reachability of the base /fapi/v1/algoOrder resource, confirmed live:
        # a parameter-validation error (-1102, missing algoid/clientalgoid) is
        # real proof the endpoint exists for this key, not a 404. This is NOT
        # a "list open algo orders" call - no confirmed listing endpoint has
        # been found (algoOpenOrders and two other guessed paths all 404'd).
        "algo_api_reachable": algo_probe["value"]["reachable"] if algo_probe["available"] else None,
        "algo_api_evidence": algo_probe["value"]["evidence"] if algo_probe["available"] else None,
        "algo_api_error": algo_probe.get("reason"),
    }


def build_protection_provider_diagnostics(mode: str, db) -> dict:
    """Phase 26 Algo Order Provider: which surface this mode's account is
    known (or assumed) to need, when/why that was detected, and the most
    recent real protection placement's provider/response/latency - straight
    from app.trading.protection_capability and the BinanceBotTrade journal,
    never re-derived or guessed here."""
    from app.db.models import BinanceBotTrade
    from app.trading import protection_capability

    cap = protection_capability.snapshot(mode)
    provider = cap["provider"] or "auto"  # undetected yet - the router tries classic first

    last_trade = (
        db.query(BinanceBotTrade)
        .filter(BinanceBotTrade.mode == mode, BinanceBotTrade.provider_used.isnot(None))
        .order_by(BinanceBotTrade.id.desc())
        .first()
    )
    last_algo_response = None
    if last_trade and last_trade.provider_used == "algo":
        last_algo_response = last_trade.provider_response

    return {
        "mode": mode,
        "provider": provider,
        "capability": cap,
        "last_provider_used": last_trade.provider_used if last_trade else None,
        "last_provider_response": last_trade.provider_response if last_trade else None,
        "last_algo_response": last_algo_response,
        "last_provider_latency_ms": last_trade.provider_latency_ms if last_trade else None,
        "last_verification_result": last_trade.verification_result if last_trade else None,
    }


def last_protective_attempts(db) -> dict:
    """Step 2 (persisted): the most recent real TP/SL placement attempt's
    exact stage-by-stage record, straight from BinanceExecutionAttempt -
    already-redacted (never contains keys/secrets, see execution_pipeline.py)."""
    from app.db.models import BinanceExecutionAttempt

    row = (
        db.query(BinanceExecutionAttempt)
        .filter(BinanceExecutionAttempt.final_status.in_(["PROTECTION_FAILED", "order_accepted"]))
        .order_by(BinanceExecutionAttempt.id.desc())
        .first()
    )
    if not row:
        return {"available": False, "reason": "No protective order attempt recorded yet"}

    stages = {s["stage"]: s for s in (row.stages or [])}
    return {
        "available": True,
        "attempt_id": row.id,
        "symbol": row.symbol,
        "side": row.side,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "final_status": row.final_status,
        "final_reason": row.final_reason,
        "protective_tp_stage": stages.get("protective_tp_submitted"),
        "protective_sl_stage": stages.get("protective_sl_submitted"),
        "protection_confirmed_stage": stages.get("protection_confirmed"),
    }


# ================================================== Debug 2.pdf sections 7-8-11
#
# Position mode (hedge vs one-way) is a per-account Binance setting, not a
# per-order one. Nothing in this app's live order-placement path ever
# calls the mutating POST /fapi/v1/positionSide/dual - every order already
# adapts its own parameters to whatever mode get_position_mode() (a plain
# read) currently reports, see execution_router.open_position and
# app.exchanges.binance_futures_client._place_protective_order. The actual
# root cause of the observed "Position side cannot be changed if there
# exists open orders." rejection was margin type / leverage - a DIFFERENT
# pair of per-symbol account settings that WAS being (re-)asserted on
# every entry via set_margin_type/set_leverage, which Binance refuses to
# change while a position or resting order already exists for that
# symbol - fixed at the source in real_risk_gate.py's
# existing_position_same_symbol check.
#
# ensure_position_mode_for_trading() exists for the OTHER interpretation
# of this bug (section 8's literal ask): a safety guard for any FUTURE
# admin "switch position mode" action, so it inherits this exact rule
# instead of ever repeating the mistake this bug is named after - it is
# not wired into the live order-placement path today because nothing
# there needs it (this app adapts to the current mode rather than
# requiring one).

async def ensure_position_mode_for_trading(
    client: BinanceFuturesClient, expected_hedge_mode: bool | None = None,
) -> dict:
    """Read-only safety guard - NEVER itself calls the mutating position-
    mode endpoint. Returns PASS when there's nothing to reconcile (no
    expectation given, or the account already matches it); BLOCKED when a
    mismatch exists AND open positions/orders would make Binance refuse
    the change anyway; SETUP_REQUIRED when a mismatch exists but the
    account is empty enough that an explicit admin action could safely
    fix it. A caller that actually wants to change the mode still has to
    call client._post("/fapi/v1/positionSide/dual", ...) itself, deliberately,
    only when this returns SETUP_REQUIRED."""
    current_hedge_mode = await client.get_position_mode()
    current_label = "HEDGE" if current_hedge_mode else "ONE-WAY"

    if expected_hedge_mode is None or current_hedge_mode == expected_hedge_mode:
        return {
            "status": "PASS", "current_mode": current_label, "expected_mode": current_label,
            "open_positions_count": None, "open_orders_count": None,
            "mode_change_allowed": None, "reason": None,
        }

    expected_label = "HEDGE" if expected_hedge_mode else "ONE-WAY"
    positions = await client.get_positions()
    open_orders = await client.get_open_orders()
    if positions or open_orders:
        return {
            "status": "BLOCKED", "current_mode": current_label, "expected_mode": expected_label,
            "open_positions_count": len(positions), "open_orders_count": len(open_orders),
            "mode_change_allowed": False,
            "reason": "Position mode cannot be changed while open orders or positions exist. "
                      "Cancel orders/close positions first, then run setup.",
        }
    return {
        "status": "SETUP_REQUIRED", "current_mode": current_label, "expected_mode": expected_label,
        "open_positions_count": 0, "open_orders_count": 0,
        "mode_change_allowed": True,
        "reason": "Mode mismatch with no open positions/orders - an explicit admin setup action may change it.",
    }


async def build_position_mode_diagnostics(client: BinanceFuturesClient) -> dict:
    """Section 11 diagnostics panel: current mode + how many positions/
    orders exist right now (informational - this app has no single
    "expected" mode of its own, see the module note above)."""
    mode_result = await _safe(ensure_position_mode_for_trading(client))
    positions_result = await _safe(client.get_positions())
    orders_result = await _safe(client.get_open_orders())

    open_positions_count = len(positions_result["value"]) if positions_result["available"] else None
    open_orders_count = len(orders_result["value"]) if orders_result["available"] else None

    return {
        "current_mode": mode_result["value"]["current_mode"] if mode_result["available"] else None,
        "current_mode_error": mode_result.get("reason"),
        "expected_mode": None,  # no enforced expectation - this app adapts to whichever mode is set
        "open_orders_count": open_orders_count,
        "open_positions_count": open_positions_count,
        "mode_change_allowed": bool(open_positions_count == 0 and open_orders_count == 0)
        if open_positions_count is not None and open_orders_count is not None else None,
        "last_mode_change_attempt": None,  # no code path ever attempts one - see module note
        "reason": (
            "Position mode changes are blocked while any position or open order exists"
            if (open_positions_count or open_orders_count)
            else "No open positions/orders - mode changes would be permitted if ever attempted"
        ),
    }
