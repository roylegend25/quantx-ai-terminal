"""Trading mode toggle, live unlock/lock, kill switch and real-order
actions (Phase 23).

User-facing modes are PAPER and BINANCE_LIVE only (BINANCE_TESTNET stays a
developer-internal mode reachable via app.trading.modes.set_mode, never via
this API). Every route sits behind authentication (router-level dependency
in app/main.py). Real orders exist only behind the execution router, whose
providers enforce the real risk gate internally - none of these endpoints
carries a bypass parameter.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.bot import BOT_STATE, update_state
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import verify_password
from app.db.models import BinanceBotTrade, BinanceExecutionAttempt, TradingAuditLog
from app.db.session import get_db
from app.monitoring.logging import get_logger
from app.risk import settings_repository
from app.trading import margin_calculator as mc
from app.trading import modes, protection, protection_provider
from app.trading.execution_pipeline import STAGE_ORDER
from app.trading.execution_router import _safe_error, _tracked_algo_id, router as execution_router
from app.trading.margin import RiskValidationError, validate_risk_levels

router = APIRouter(prefix="/api/trading", tags=["trading"])
# Debug 1.pdf: previously `logging.getLogger(...)` directly - that logger
# has no handler and no configured level of its own, so it silently
# inherits the root logger's default WARNING and every .debug() call was a
# no-op that never reached `docker logs quantx-backend`. get_logger()
# (app.monitoring.logging) is what every other module in this app uses -
# it attaches the real stdout/JSON handler and sets INFO, so these calls
# are now actually visible.
logger = get_logger("quantx.trading_control")


# ================================================================== mode

@router.get("/mode")
async def get_mode(db: Session = Depends(get_db)):
    """The one endpoint nearly every page polls for trading status
    (TradingShared.useTradingStatus) - merges in the canonical Binance
    connectivity fields (app.api.exchange.binance_connectivity) so
    `binance_connected` and friends are never stale/undefined here like
    they used to be. Keeps modes.exchange_safe_status() itself untouched
    (still used synchronously elsewhere) - only this GET handler awaits
    the connectivity probe."""
    from app.api.exchange import binance_connectivity

    safe = modes.exchange_safe_status(db)
    safe.update(await binance_connectivity())
    return safe


class ModeRequest(BaseModel):
    mode: str


@router.post("/mode")
async def set_mode(body: ModeRequest, db: Session = Depends(get_db)):
    """Switch the active bot execution mode. Selecting BINANCE_LIVE only
    *requests* live: it stays BINANCE_LIVE_LOCKED (viewing allowed, trading
    blocked) until the unlock ceremony completes AND the server env lock is
    open. Selecting PAPER always works and re-arms the live lock."""
    mode = body.mode.upper()
    if mode not in modes.USER_SELECTABLE_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {', '.join(modes.USER_SELECTABLE_MODES)}")
    if mode == modes.MODE_LIVE and not modes.binance_configured():
        raise HTTPException(status_code=400, detail="Binance API keys are not configured on the server")

    control = modes.set_mode(mode, db=db)
    return {"ok": True, "control": control, "status": modes.exchange_safe_status(db)}


# ============================================================ live unlock

class LiveUnlockRequest(BaseModel):
    # Phase 31: a live-authorization lease additionally requires fresh
    # password re-authentication, a SECOND distinct typed phrase, and
    # explicit account + symbol confirmation - none of these existed before
    # the incident that motivated this. All five inputs plus every
    # acknowledgement must be correct in the SAME request; there is no
    # partial-credit path.
    password: str = ""
    confirmation: str = ""
    second_confirmation: str = ""
    account_confirmation: str = ""
    symbol_confirmation: str = ""
    acknowledgements: dict[str, bool] = {}


@router.post("/binance/unlock-live")
async def unlock_live(
    body: LiveUnlockRequest, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)
):
    """The only path to a live-order-authorization lease (Phase 31).
    Requires the server env lock, live-write credentials, a fresh password
    re-check, both typed phrases, account + symbol confirmation, and every
    acknowledgement - all in the same request. Success grants a lease that
    expires on its own (settings.live_lease_ttl_seconds) and is consumed
    after one real order (settings.live_lease_max_actions); it does not
    survive a restart. A failure of any single check is audited as
    live_unlock_rejected with the specific reason."""

    def rejected(reason: str, status_code: int = 400):
        modes.audit("live_unlock_rejected", detail={"user": current_user, "reason": reason}, db=db)
        raise HTTPException(status_code=status_code, detail=reason)

    if not settings.binance_live_enabled:
        rejected(
            "Live trading disabled by server configuration. Set BINANCE_LIVE_ENABLED=true in the backend .env only when ready.",
            status_code=403,
        )
    if not modes.binance_live_configured():
        rejected(
            "Live-write credentials are not configured on the server "
            "(BINANCE_LIVE_API_KEY / BINANCE_LIVE_API_SECRET) - the shared read-only/testnet key is never used for live orders.",
            status_code=400,
        )
    if not verify_password(body.password, settings.admin_password_hash):
        rejected("Password re-authentication failed", status_code=401)
    if body.confirmation.strip() != modes.LIVE_UNLOCK_PHRASE:
        rejected(f'Type exactly "{modes.LIVE_UNLOCK_PHRASE}" to confirm')
    if body.second_confirmation.strip() != modes.LIVE_LEASE_SECOND_CONFIRMATION_PHRASE:
        rejected(f'Type exactly "{modes.LIVE_LEASE_SECOND_CONFIRMATION_PHRASE}" as the second confirmation')
    if body.account_confirmation.strip() != current_user:
        rejected(f"Account confirmation must exactly match your username ({current_user})")
    symbol_confirmation = body.symbol_confirmation.strip().upper()
    if symbol_confirmation not in (*settings.binance_allowed_symbols, "ALL"):
        rejected(
            f"Symbol confirmation must be one of {', '.join(settings.binance_allowed_symbols)} or ALL"
        )
    missing = [k for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS if not body.acknowledgements.get(k)]
    if missing:
        rejected(f"All safety acknowledgements must be checked (missing: {', '.join(missing)})")

    control = modes.unlock_live(db=db)
    lease = modes.create_live_lease(user=current_user, symbol_scope=symbol_confirmation, db=db)
    return {
        "ok": True,
        "message": f"LIVE trading authorized for {lease['seconds_remaining']}s or one order, whichever comes first. Real funds are now at risk.",
        "control": control,
        "lease": lease,
        "status": modes.exchange_safe_status(db),
    }


@router.get("/binance/live-lease-status")
async def live_lease_status(db: Session = Depends(get_db)):
    """Current live-authorization lease (if any) for the frontend's
    lease-expiry countdown. Never returns anything beyond what
    exchange_safe_status already exposes publicly."""
    return {"lease": modes.get_active_lease(db)}


@router.post("/binance/lock-live")
async def lock_live(db: Session = Depends(get_db)):
    """Re-arm the live lock and drop back to PAPER execution immediately."""
    control = modes.set_mode(modes.MODE_PAPER, db=db)
    return {"ok": True, "message": "Live trading locked - back to paper execution", "control": control,
            "status": modes.exchange_safe_status(db)}


# ======================================================= real-order actions

def _require_live(db) -> None:
    mode = modes.effective_mode(db)
    if mode != modes.MODE_LIVE:
        reason = (
            "Live trading disabled by server configuration"
            if not settings.binance_live_enabled
            else "Binance live trading is locked - complete the risk acknowledgement first"
        )
        if mode == modes.MODE_PAPER:
            reason = "Active mode is PAPER - switch to Binance Real Money first"
        raise HTTPException(status_code=409, detail=reason)


def _require_cancelable(db) -> None:
    """Canceling a resting real order is a de-risking safety action, not
    order placement - see Debug 1.pdf section 2 and
    execution_router.ExecutionRouter._cancel_provider. Unlike
    _require_live, this must NOT depend on the bot's active trading mode
    (PAPER/TESTNET/LIVE/LIVE_LOCKED): "Real Open Orders" always reflects
    the real Binance account regardless of active mode, so the cancel
    button next to it must too. The only hard blockers are the ones that
    make it genuinely impossible to reach Binance safely right now."""
    if not modes.binance_configured():
        raise HTTPException(status_code=400, detail="Binance API credentials are not configured")
    from app.exchanges.binance_rate_limiter import limiter
    status = limiter.status()
    if status.get("banned"):
        raise HTTPException(
            status_code=429,
            detail=f"Binance temporary IP ban active - retry in {status.get('retry_after_seconds')}s",
        )


class ClosePositionRequest(BaseModel):
    position_id: int | None = None
    symbol: str | None = None
    quantity: float | None = None
    confirm: bool = False


@router.post("/binance/close-position")
async def binance_close_position(body: ClosePositionRequest, db: Session = Depends(get_db)):
    _require_live(db)
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation required to close a real position (confirm=true)")
    result = await execution_router.close_position(
        position_id=body.position_id, symbol=body.symbol, quantity=body.quantity
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Close failed")
    return result.to_dict()


class PositionRiskUpdate(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@router.patch("/binance/positions/{position_id}/risk")
async def binance_update_position_risk(position_id: int, body: PositionRiskUpdate, db: Session = Depends(get_db)):
    """Cancel-and-replace real reduce-only TP/SL orders on Binance. Local
    state updates only after the exchange confirms."""
    _require_live(db)
    fields = body.model_fields_set
    if not fields:
        raise HTTPException(status_code=400, detail="Provide stop_loss and/or take_profit")

    results = {}
    if "stop_loss" in fields:
        r = await execution_router.update_stop_loss(position_id=position_id, stop_loss=body.stop_loss)
        if not r.ok:
            raise HTTPException(status_code=400, detail=r.reason or "Stop-loss update failed")
        results["stop_loss"] = r.to_dict()
    if "take_profit" in fields:
        r = await execution_router.update_take_profit(position_id=position_id, take_profit=body.take_profit)
        if not r.ok:
            raise HTTPException(status_code=400, detail=r.reason or "Take-profit update failed")
        results["take_profit"] = r.to_dict()
    return {"ok": True, **results}


class CancelOrderRequest(BaseModel):
    # symbol/order_id are Optional here (not required) purely so a missing
    # value fails with a safe, explicit 400 below instead of FastAPI's
    # generic 422 - see Debug 1.pdf section 3 ("missing symbol returns
    # 400"). provider, when given, must be "classic" or "algo"; when
    # omitted it's inferred from whichever identifiers are present -
    # classic needs order_id or client_order_id, algo needs algo_id or
    # client_algo_id (Debug 1.pdf section 2).
    symbol: str | None = None
    provider: str | None = None
    order_id: int | None = None
    client_order_id: str | None = None
    algo_id: int | None = None
    client_algo_id: str | None = None


@router.post("/binance/cancel-order")
async def binance_cancel_order(body: CancelOrderRequest, db: Session = Depends(get_db)):
    _require_cancelable(db)
    if not body.symbol:
        raise HTTPException(status_code=400, detail="Cannot cancel: missing symbol")
    if body.provider is not None and body.provider not in ("classic", "algo"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel: invalid provider '{body.provider}'")

    has_classic_id = body.order_id is not None or body.client_order_id is not None
    has_algo_id = body.algo_id is not None or body.client_algo_id is not None
    if body.provider == "classic" and not has_classic_id:
        raise HTTPException(status_code=400, detail="Cannot cancel: missing order identifier")
    if body.provider == "algo" and not has_algo_id:
        raise HTTPException(status_code=400, detail="Cannot cancel: missing order identifier")
    if body.provider is None and not has_classic_id and not has_algo_id:
        raise HTTPException(status_code=400, detail="Cannot cancel: missing order identifier")

    # Debug 1.pdf section 2/9: safe request-shape logging only - symbol/
    # provider/which-identifier-was-present as booleans, never the actual
    # identifier values, and never keys/signatures/secrets. INFO (not
    # DEBUG) so this is actually visible in `docker logs quantx-backend`
    # without changing the process's log level - see get_logger() above.
    logger.info(
        "binance_cancel_order request symbol_present=%s provider=%s has_order_id=%s "
        "has_client_order_id=%s has_algo_id=%s has_client_algo_id=%s",
        bool(body.symbol), body.provider, body.order_id is not None, body.client_order_id is not None,
        body.algo_id is not None, body.client_algo_id is not None,
    )

    result = await execution_router.cancel_order(
        symbol=body.symbol, order_id=body.order_id, client_order_id=body.client_order_id,
        algo_id=body.algo_id, client_algo_id=body.client_algo_id, provider=body.provider,
    )
    result_detail = result.detail or {}
    endpoint_selected = "algo" if "algo_id" in (result_detail.get("order") or {}) else "classic"
    logger.info(
        "binance_cancel_order response symbol_present=%s ok=%s endpoint_selected=%s reason=%s",
        bool(body.symbol), result.ok, endpoint_selected if result.ok else None,
        result.reason if not result.ok else None,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel failed")
    return result.to_dict()


class CancelAllRequest(BaseModel):
    symbol: str | None = None
    confirm: bool = False


@router.post("/binance/cancel-all-orders")
async def binance_cancel_all_orders(body: CancelAllRequest, db: Session = Depends(get_db)):
    # Deliberately NOT implemented as "for each open order, call the same
    # single-order cancel function" - it calls Binance's own native bulk
    # DELETE /fapi/v1/allOpenOrders instead (one signed call cancels every
    # resting order for a symbol). A client-side loop would multiply
    # rate-limit pressure exactly where section 8 asks for rate-limit
    # safety, and would change the atomicity/partial-failure behavior of a
    # path already confirmed working - which section "Do NOT break Cancel
    # All" forbids risking. The two paths already share the one thing that
    # actually matters - identifier/provider resolution - via
    # ExecutionRouter._cancel_provider.
    _require_cancelable(db)
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation required to cancel all real orders (confirm=true)")
    result = await execution_router.cancel_all_orders(symbol=body.symbol)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel-all failed")
    return result.to_dict()


@router.get("/binance/open-orders/debug")
async def binance_open_orders_debug():
    """Debug 1.pdf section 7: proves, for every currently open Binance
    order, exactly which cancel identifiers the row-level Cancel button
    actually has to work with - reads the same cached snapshot GET
    /binance/orders already serves (no extra Binance calls, no secrets)."""
    from app.api.portfolio import binance_orders as _binance_orders

    orders_response = await _binance_orders()
    debug_rows = []
    for o in orders_response.get("orders", []):
        has_order_id = o.get("order_id") is not None
        has_client_order_id = o.get("client_order_id") is not None
        has_algo_id = o.get("algo_id") is not None
        has_client_algo_id = o.get("client_algo_id") is not None
        debug_rows.append({
            "symbol": o.get("symbol"),
            "type": o.get("type"),
            "provider": o.get("provider"),
            "has_order_id": has_order_id,
            "has_client_order_id": has_client_order_id,
            "has_algo_id": has_algo_id,
            "has_client_algo_id": has_client_algo_id,
            "cancel_payload_preview": {
                "symbol": o.get("symbol"),
                "provider": o.get("provider"),
                "order_id_present": has_order_id,
                "client_order_id_present": has_client_order_id,
                "algo_id_present": has_algo_id,
                "client_algo_id_present": has_client_algo_id,
            },
        })
    return {"orders": debug_rows}


# ============================================================ kill switch

class KillSwitchRequest(BaseModel):
    active: bool = True
    reason: str | None = None
    # cancel resting exchange orders as part of the emergency stop -
    # positions are NEVER auto-closed by the kill switch
    cancel_orders: bool = True


@router.post("/kill-switch")
async def kill_switch(body: KillSwitchRequest, db: Session = Depends(get_db)):
    """Emergency stop: halts the bot and blocks ALL new trades - paper and
    real. Optionally cancels resting exchange orders; closing real
    positions always requires the separate close-position endpoint with its
    own confirmation."""
    control = modes.set_kill_switch(body.active, reason=body.reason or ("manual" if body.active else None), db=db)

    canceled = None
    if body.active:
        BOT_STATE["status"] = "stopped"
        update_state("kill_switch")
        if body.cancel_orders and modes.effective_mode(db) in modes.REAL_MODES:
            result = await execution_router.cancel_all_orders()
            canceled = result.to_dict()

    return {
        "ok": True,
        "message": "Kill switch ACTIVATED - all trading halted" if body.active else "Kill switch deactivated",
        "control": control,
        "canceled_orders": canceled,
    }


@router.post("/sync")
async def sync_now():
    """Manual position/order re-sync from Binance (no-op in paper mode)."""
    positions = await execution_router.sync_positions()
    orders = await execution_router.sync_orders()
    return {"positions": positions.to_dict(), "orders": orders.to_dict()}


# =================================================== Binance Live tab reads
#
# Three read-only, non-admin aggregation endpoints backing the Binance Live
# tabs (Positions/Performance/Bot Settings/Risk/Execution) and the readiness
# checklist on the Binance Real page. Every value here is assembled from
# modules that already enforce the no-secrets guarantee (modes.py,
# portfolio.py's read-only client, real_risk_gate/execution_router's audit
# trail) - nothing new touches Binance credentials.

@router.get("/live-readiness")
async def live_readiness(db: Session = Depends(get_db)):
    """Pass/fail checklist for whether a real order could be placed right
    now. Mirrors modes.can_trade()'s checks but itemized so the UI can show
    exactly which gate is blocking, not just the first one."""
    from app.api.exchange import _binance_connected
    from app.api.portfolio import get_read_client
    from app.exchanges.binance_snapshot_service import snapshot_service

    configured = modes.binance_configured()
    connected = await _binance_connected() if configured else False
    control = modes.get_control(db)
    mode = modes.effective_mode(db)
    lease = modes.get_active_lease(db)

    risk_limits_valid = (
        settings.binance_max_leverage >= 1
        and settings.binance_max_notional_per_trade > 0
        and settings.binance_max_daily_loss_usdt > 0
        and bool(settings.binance_allowed_symbols)
    )

    balance_available = False
    balance_detail = "Binance API key not configured"
    if configured:
        try:
            # Phase 29: shares the same cached balances read as
            # /api/portfolio/binance/balances instead of its own signed
            # call - this endpoint is polled every 10s alongside several
            # others that all want the same number.
            balances = await snapshot_service.get_balances(get_read_client())
            usdt_available = next((b.available for b in balances if b.asset == "USDT"), 0.0)
            balance_available = usdt_available > 0
            balance_detail = (
                f"${usdt_available:.2f} USDT available" if usdt_available > 0 else "No available USDT balance"
            )
        except Exception:
            balance_detail = "Could not verify balance"

    steps = [
        {
            "key": "binance_api_configured", "label": "Binance API configured", "passed": configured,
            "detail": "API key and secret present" if configured else "BINANCE_API_KEY / BINANCE_API_SECRET not set",
        },
        {
            "key": "binance_account_connected", "label": "Binance account connected", "passed": connected,
            "detail": "Signed read succeeded" if connected else "Could not reach or authenticate to Binance",
        },
        {
            "key": "server_live_lock", "label": "Server live lock enabled", "passed": settings.binance_live_enabled,
            "detail": "BINANCE_LIVE_ENABLED=true" if settings.binance_live_enabled else "BINANCE_LIVE_ENABLED=false",
        },
        {
            "key": "user_live_unlock", "label": "User live confirmation completed", "passed": control["live_unlocked"],
            "detail": "Live-risk ceremony completed" if control["live_unlocked"] else "Live-risk ceremony not completed",
        },
        {
            "key": "active_mode_binance_live", "label": "Active mode is Binance Live", "passed": mode == modes.MODE_LIVE,
            "detail": mode,
        },
        {
            "key": "live_credentials_configured", "label": "Live-write credentials configured",
            "passed": modes.binance_live_configured(db),
            "detail": "Credential present (env or saved securely)" if modes.binance_live_configured(db)
            else "No live-write credential - save one from the Binance Real Credentials panel",
        },
        {
            "key": "live_authorization_lease", "label": "Live-authorization lease active",
            "passed": lease is not None,
            "detail": f"Expires in {lease['seconds_remaining']}s, {lease['actions_remaining']} action(s) left" if lease
            else "No active lease - re-confirm live trading to authorize the next order",
        },
        {
            "key": "kill_switch_off", "label": "Kill switch off", "passed": not control["kill_switch_active"],
            "detail": "Active" if control["kill_switch_active"] else "Off",
        },
        {
            "key": "risk_limits_valid", "label": "Risk limits valid", "passed": risk_limits_valid,
            "detail": "Leverage, notional, daily-loss and symbols configured" if risk_limits_valid
            else "One or more risk limits are unset or invalid",
        },
        {
            "key": "balance_available", "label": "Balance available", "passed": balance_available,
            "detail": balance_detail,
        },
    ]
    blocked = next((s["label"] for s in steps if not s["passed"]), None)
    return {"ok": blocked is None, "steps": steps, "blocked_reason": blocked}


# ------------------------------------------------------- live risk scoring
#
# Transparent 0-100 composite score backing the Binance Live tab's risk
# gauge. Every component is a ratio of real, currently-observed account
# data - never fabricated - and the whole score is hidden (None, level
# "UNKNOWN") whenever the account itself isn't reachable, since a precise
# number with no data behind it would be misleading.
#
# Weights (sum to 100):
#   margin usage            25  - initial margin used / margin balance
#   daily loss usage        20  - realized loss today / configured max daily loss
#   notional exposure       15  - total notional / (max notional per trade x max open positions)
#   leverage usage          10  - current effective leverage / configured max leverage
#   liquidation proximity   15  - how close the nearest open position is to liquidation
#   open-position count      5  - open positions / max open positions
#   readiness failures      10  - fraction of {server lock, user unlock, active
#                                 mode, kill switch, account connected} not OK
#
# Classification: 0-25 LOW, 26-50 MEDIUM, 51-75 HIGH, 76-100 CRITICAL.
_RISK_WEIGHT_MARGIN = 25.0
_RISK_WEIGHT_DAILY_LOSS = 20.0
_RISK_WEIGHT_NOTIONAL = 15.0
_RISK_WEIGHT_LEVERAGE = 10.0
_RISK_WEIGHT_LIQUIDATION = 15.0
_RISK_WEIGHT_POSITIONS = 5.0
_RISK_WEIGHT_READINESS = 10.0
_READINESS_GATE_COUNT = 5  # server lock, user unlock, active mode, kill switch, account connected


def _risk_level_of(score: float) -> str:
    if score <= 25:
        return "LOW"
    if score <= 50:
        return "MEDIUM"
    if score <= 75:
        return "HIGH"
    return "CRITICAL"


def _binance_readiness_reasons(safe: dict, account_connected: bool) -> list[str]:
    """Non-account-data readiness gates - always computable, never faked."""
    reasons: list[str] = []
    if not safe["binance_live_enabled_by_server"]:
        reasons.append("Server Live Lock is OFF")
    if not safe["binance_live_unlocked_by_user"]:
        reasons.append("User Live Unlock is LOCKED")
    if safe["active_mode"] != modes.MODE_LIVE:
        reasons.append(f"Active mode is {safe['active_mode']}")
    if safe["kill_switch_active"]:
        reasons.append("Kill switch is active")
    if not modes.binance_configured():
        reasons.append("Binance API keys are not configured")
    elif not account_connected:
        reasons.append("Binance account is not connected")
    return reasons


def _compute_binance_risk_score(
    *, safe: dict, summary: dict, risk_settings: dict, account_connected: bool, current_leverage: float | None,
) -> tuple[float | None, str, list[str]]:
    reasons = _binance_readiness_reasons(safe, account_connected)

    if not account_connected or not summary.get("available"):
        # Account-data components can't be computed honestly - surface the
        # readiness reasons only, no numeric score.
        return None, "UNKNOWN", reasons

    max_daily_loss = settings.binance_max_daily_loss_usdt
    daily_pnl = summary.get("daily_pnl")
    daily_loss_used = -daily_pnl if isinstance(daily_pnl, (int, float)) and daily_pnl < 0 else 0.0
    if max_daily_loss > 0 and daily_loss_used >= max_daily_loss:
        reasons.append("Daily loss limit reached")

    max_open_positions = risk_settings["max_open_positions"]
    open_positions = summary.get("open_positions") or 0
    if max_open_positions > 0 and open_positions >= max_open_positions:
        reasons.append("Max open positions reached")

    available_balance = summary.get("available_balance")
    if available_balance is not None and available_balance <= 0:
        reasons.append("Balance insufficient")

    margin_balance = summary.get("margin_balance") or 0.0
    margin_used = summary.get("margin_used") or 0.0
    margin_component = min(1.0, margin_used / margin_balance) if margin_balance > 0 else 0.0

    daily_component = min(1.0, daily_loss_used / max_daily_loss) if max_daily_loss > 0 else 0.0

    max_notional_pool = settings.binance_max_notional_per_trade * max(max_open_positions, 1)
    notional_component = (
        min(1.0, (summary.get("total_notional_exposure") or 0.0) / max_notional_pool)
        if max_notional_pool > 0 else 0.0
    )

    max_leverage = settings.binance_max_leverage
    leverage_component = min(1.0, (current_leverage or 0.0) / max_leverage) if max_leverage > 0 else 0.0

    nearest_liq = summary.get("nearest_liquidation_distance_pct")
    # Liquidation risk climbs as distance shrinks; 10% away is treated as
    # the "already at max risk" edge (matches the UI's own liquidation
    # warning threshold, not an invented number).
    liq_component = max(0.0, min(1.0, (10.0 - nearest_liq) / 10.0)) if nearest_liq is not None else 0.0

    position_component = min(1.0, open_positions / max_open_positions) if max_open_positions > 0 else 0.0

    readiness_component = min(1.0, len(_binance_readiness_reasons(safe, account_connected)) / _READINESS_GATE_COUNT)

    score = (
        margin_component * _RISK_WEIGHT_MARGIN
        + daily_component * _RISK_WEIGHT_DAILY_LOSS
        + notional_component * _RISK_WEIGHT_NOTIONAL
        + leverage_component * _RISK_WEIGHT_LEVERAGE
        + liq_component * _RISK_WEIGHT_LIQUIDATION
        + position_component * _RISK_WEIGHT_POSITIONS
        + readiness_component * _RISK_WEIGHT_READINESS
    )
    score = round(min(100.0, score), 1)
    return score, _risk_level_of(score), reasons


@router.get("/binance/risk-status")
async def binance_risk_status(db: Session = Depends(get_db)):
    """Aggregated real-money risk snapshot for the Risk Management page's
    Binance tab - one call instead of hand-assembling from /mode +
    /portfolio/binance/summary on the client. Every value is read from live
    Binance account state (or the server's own trading-control state) -
    nothing here is copied from the paper ledger."""
    from app.api.portfolio import binance_orders, binance_summary, get_account_snapshot, get_read_client

    safe = modes.exchange_safe_status(db)
    summary = await binance_summary(db=db)
    orders = await binance_orders()
    risk = settings_repository.get_settings(scope="binance_real", db=db)

    daily_pnl = summary.get("daily_pnl")
    daily_loss_used = round(-daily_pnl, 2) if isinstance(daily_pnl, (int, float)) and daily_pnl < 0 else 0.0

    account_connected = bool(summary.get("available"))
    current_leverage = None
    if account_connected and modes.binance_configured():
        try:
            # Phase 29: binance_summary() above already refreshed (or
            # served the cached) account bundle - re-reading it here costs
            # no extra Binance call within the TTL window instead of a
            # third independent get_positions() for the same data.
            _, positions, _ = await get_account_snapshot(get_read_client())
            if positions:
                current_leverage = max(p.leverage for p in positions)
        except Exception:
            current_leverage = None

    risk_score, risk_level, blocked_reasons = _compute_binance_risk_score(
        safe=safe, summary=summary, risk_settings=risk,
        account_connected=account_connected, current_leverage=current_leverage,
    )

    margin_balance = summary.get("margin_balance")
    margin_used = summary.get("margin_used")
    margin_usage_pct = (
        round(margin_used / margin_balance * 100, 2) if margin_balance and margin_used is not None else None
    )
    daily_loss_used_pct = (
        round(daily_loss_used / settings.binance_max_daily_loss_usdt * 100, 2)
        if settings.binance_max_daily_loss_usdt > 0 else None
    )

    return {
        **safe,
        "available": account_connected,
        "account_connected": account_connected,
        "reason": summary.get("reason"),
        "risk_level": risk_level,
        "risk_score": risk_score,
        "blocked_reasons": blocked_reasons,
        "wallet_balance": summary.get("total_wallet_balance"),
        "available_balance": summary.get("available_balance"),
        "margin_balance": summary.get("margin_balance"),
        "free_margin": summary.get("free_margin"),
        "margin_used": summary.get("margin_used"),
        "margin_usage_pct": margin_usage_pct,
        "unrealized_pnl": summary.get("unrealized_pnl"),
        "daily_pnl": daily_pnl,
        "daily_loss_used_usdt": daily_loss_used,
        "daily_loss_used_pct": daily_loss_used_pct,
        "total_notional_exposure": summary.get("total_notional_exposure"),
        "nearest_liquidation_distance_pct": summary.get("nearest_liquidation_distance_pct"),
        "current_effective_leverage": current_leverage,
        "open_positions": summary.get("open_positions"),
        "max_open_positions": risk["max_open_positions"],
        "open_orders": len(orders.get("orders", [])) if orders.get("available") else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/binance/decision-status")
async def binance_decision_status(symbol: str | None = None, db: Session = Depends(get_db)):
    """Latest live bot intent and real risk-gate verdict for the Binance
    Live tab's Bot Decision Status card (and the Live Margin Calculator's
    pre-trade simulation, which calls this directly for whichever symbol is
    on screen - defaults to settings.default_symbol when omitted).

    Model/strategy direction and confidence come from the same prediction
    pipeline every mode shares (app.api.prediction) - the bot evaluates one
    signal per symbol per cycle regardless of active mode, so re-reading
    that signal here is not "reusing paper state", it is the live signal.
    What IS strictly live-only is the gate verdict layered on top: whether
    this mode, the server/user locks, the kill switch and the real Binance
    account would actually let the order through right now. No paper
    position, ledger or P&L value is read anywhere in this function."""
    from app.api.prediction import prediction as compute_prediction
    from app.api.portfolio import binance_summary

    symbol = (symbol or settings.default_symbol).upper()
    from app.trading_horizon.current_authority import resolve_authoritative_timeframe
    timeframe_resolution = await resolve_authoritative_timeframe(db, settings.admin_username, symbol)
    timeframe = timeframe_resolution["execution_timeframe"]
    try:
        # `prediction()`'s current_user parameter is a FastAPI dependency
        # (Depends(_prediction_subject)) - calling the route function
        # directly like this bypasses FastAPI's DI entirely, so without an
        # explicit value it stays the raw, unresolved Depends(...) sentinel
        # object rather than a real subject string. That object then fails
        # deep inside a SQLAlchemy query (binding it as a parameter raises
        # sqlite3.ProgrammingError), which this bare except silently turned
        # into "no prediction available" - permanently, for every symbol,
        # every call, regardless of the real Active Drive V2 decision.
        # settings.admin_username is exactly what _prediction_subject
        # itself resolves to for an unauthenticated/direct call (see its
        # docstring) - this is not a new fallback, just making explicit
        # what the dependency already does.
        pred = await compute_prediction(symbol, interval=timeframe, current_user=settings.admin_username)
    except Exception:
        pred = None

    # GET /api/prediction/{symbol} wraps the actual make_prediction() output
    # under "prediction" (top-level direction/confidence/target/stop/
    # decision_engine are convenience mirrors of the same values) - only
    # "computed_at" lives solely at that nested level. NO_DATA here means
    # the whole pipeline call failed outright, not merely a NO_TRADE signal
    # (a real no-candles response still carries a valid computed_at and a
    # genuine, if uninteresting, decision).
    computed_at_ms = (pred or {}).get("prediction", {}).get("computed_at") if pred else None
    if not pred or not computed_at_ms:
        return {"status": "NO_DATA", "message": "No Binance live decision has been evaluated yet."}

    decision_engine = pred.get("decision_engine") or {}
    active_model = decision_engine.get("active_model") or {}
    model_votes = decision_engine.get("model_votes") or []
    champion_vote = next((v for v in model_votes if v.get("name") == "Champion ML"), None)
    model_direction = champion_vote.get("direction") if champion_vote and champion_vote.get("available") else None

    safe = modes.exchange_safe_status(db)
    mode = safe["active_mode"]
    risk_settings = settings_repository.get_settings(scope="binance_real", db=db)
    confidence = pred.get("confidence")
    required_confidence = decision_engine.get("required_confidence")

    account_connected = False
    summary: dict = {}
    if modes.binance_configured():
        try:
            summary = await binance_summary(db=db)
            account_connected = bool(summary.get("available"))
        except Exception:
            account_connected = False

    # Phase 30, sections 7-8: the SAME shared check the real risk gate
    # enforces (protection.check_all_positions_protected), computed fresh
    # right now - never derived from a historical BinanceExecutionAttempt
    # row. This is what makes "Current Protection Check" in the UI
    # trustworthy as CURRENT, distinct from whatever an old execution
    # attempt happened to record at the time it ran.
    protection_check = {"passed": True, "detail": "No live positions to protect", "checked_positions": []}
    if account_connected and modes.binance_configured() and settings.binance_block_new_trades_if_unprotected:
        try:
            from app.api.portfolio import get_read_client
            from app.trading.execution_router import _tracked_algo_id

            client = get_read_client()
            all_protected, unprotected = await protection.check_all_positions_protected(
                client, get_tracked_algo_ids=lambda s: (
                    _tracked_algo_id(mode, s, "tp"), _tracked_algo_id(mode, s, "sl"),
                ),
            )
            if all_protected:
                protection_check = {"passed": True, "detail": "All live positions protected", "checked_positions": []}
            else:
                worst = unprotected[0]
                protection_check = {
                    "passed": False,
                    "detail": f"{worst['symbol']} is unprotected ({worst['status']})",
                    "checked_positions": unprotected,
                }
        except Exception:
            # A transient read failure here must never be shown as a
            # confident "unprotected" verdict - it's genuinely unknown.
            # The REAL gate (real_risk_gate.evaluate_real_order) still
            # fails closed on the same error; this is a preview only.
            protection_check = {"passed": None, "detail": "Could not verify protection status", "checked_positions": []}

    reasons = _binance_readiness_reasons(safe, account_connected)
    if symbol not in settings.binance_allowed_symbols:
        reasons.append(f"{symbol} is not in the allowed symbol list")
    if confidence is not None and required_confidence is not None and confidence < required_confidence:
        reasons.append(f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%")
    if account_connected and summary.get("available"):
        daily_pnl = summary.get("daily_pnl")
        daily_loss_used = -daily_pnl if isinstance(daily_pnl, (int, float)) and daily_pnl < 0 else 0.0
        if settings.binance_max_daily_loss_usdt > 0 and daily_loss_used >= settings.binance_max_daily_loss_usdt:
            reasons.append("Daily loss limit reached")
        if (summary.get("open_positions") or 0) >= risk_settings["max_open_positions"]:
            reasons.append("Max open positions reached")
        if (summary.get("available_balance") or 0) <= 0:
            reasons.append("Balance insufficient")
    if protection_check["passed"] is False:
        reasons.append(f"Existing live position is unprotected ({protection_check['detail']})")

    # Debug 2.pdf section 7/9: mirrors real_risk_gate's
    # existing_position_same_symbol check so the CURRENT blocker shown here
    # never disagrees with what the real gate will actually do - without
    # this, the UI could say "no reason to block" for a symbol that would
    # immediately fail at the Binance validation stage (margin type/
    # leverage cannot be re-asserted while a position already exists).
    if account_connected and modes.binance_configured():
        try:
            from app.api.portfolio import get_read_client
            existing_symbol_position = any(
                p.symbol == symbol for p in await get_read_client().get_positions()
            )
            if existing_symbol_position:
                reasons.append(f"{symbol} already has an open live position - no pyramiding")
        except Exception:
            pass  # a transient read failure here is not a confident "blocked" verdict either

    risk_gate_allowed = not reasons
    final_direction = "BLOCKED" if reasons else pred["direction"]

    last_trade = (
        db.query(BinanceBotTrade).order_by(BinanceBotTrade.id.desc()).first()
    )
    last_audit = (
        db.query(TradingAuditLog)
        .filter(TradingAuditLog.event.in_(
            ["order_requested", "order_accepted", "order_filled", "order_rejected", "order_blocked_by_risk_gate"]
        ))
        .filter(TradingAuditLog.mode.in_(list(modes.REAL_MODES)))
        .order_by(TradingAuditLog.id.desc())
        .first()
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "execution_timeframe_source": timeframe_resolution["source"],
        "model_direction": model_direction,
        "strategy_direction": pred["direction"],
        "final_direction": final_direction,
        "confidence": confidence,
        "required_confidence": required_confidence,
        "risk_gate_allowed": risk_gate_allowed,
        "risk_gate_reason": reasons[0] if reasons else "All live-trading risk checks passed",
        "blocked_reasons": reasons,
        # Phase 30: the CURRENT protection verdict, computed fresh via the
        # same shared resolver the real risk gate uses - never a historical
        # BinanceExecutionAttempt row. See sections 7-8: an old failed
        # attempt's reason must never be mistaken for today's blocker.
        "protection_check": protection_check,
        "active_model": active_model.get("model_type") or active_model.get("name"),
        "active_strategy": decision_engine.get("strategy_used"),
        "intended_notional": settings.binance_max_notional_per_trade,
        "intended_leverage": settings.binance_default_leverage,
        "take_profit": pred.get("target"),
        "stop_loss": pred.get("stop"),
        "last_decision_at": datetime.fromtimestamp(computed_at_ms / 1000, tz=timezone.utc).isoformat(),
        "last_execution_attempt_at": (
            last_audit.created_at.isoformat() if last_audit and last_audit.created_at else None
        ),
        "last_order_status": last_trade.status if last_trade else None,
        "active_mode": mode,
        "market_regime": decision_engine.get("market_regime"),
    }


# Execution-log event names -> pipeline stage, in the order the real order
# flow actually visits them (see execution_router.BinanceExecutionProvider).
EXECUTION_LOG_STAGES = {
    "order_requested": "intent",
    "order_blocked_by_risk_gate": "risk_gate",
    "order_notional_clamped": "risk_gate",
    "order_accepted": "router",
    "order_filled": "exchange",
    "order_rejected": "exchange",
    "position_closed": "exchange",
    "protective_order_canceled": "exchange",
    "tp_sl_updated": "exchange",
    "positions_synced": "audit",
}


@router.get("/binance/execution-log")
async def binance_execution_log(limit: int = 50, db: Session = Depends(get_db)):
    """Labeled feed of the execution pipeline (intent -> risk gate -> router
    -> exchange), built from the existing TradingAuditLog trail written by
    real_risk_gate.py and execution_router.py - no new instrumentation."""
    limit = max(1, min(limit, 200))
    rows = (
        db.query(TradingAuditLog)
        .filter(TradingAuditLog.event.in_(EXECUTION_LOG_STAGES.keys()))
        .order_by(TradingAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    events = []
    last_by_stage: dict[str, dict] = {}
    for r in rows:
        stage = EXECUTION_LOG_STAGES.get(r.event, "audit")
        entry = {
            "id": r.id,
            "event": r.event,
            "stage": stage,
            "mode": r.mode,
            "symbol": r.symbol,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        events.append(entry)
        if stage not in last_by_stage:
            last_by_stage[stage] = entry
    return {"events": events, "last_by_stage": last_by_stage}


# ================================================== execution transparency
#
# Phase 25: the decision engine's "allowed"/"trade_allowed" verdict only
# covers model/confidence/risk-settings - it has no idea whether the order
# actually reached Binance. These three endpoints close that gap using the
# BinanceExecutionAttempt instrumentation in app.trading.execution_pipeline
# / app.trading.execution_router.BinanceExecutionProvider.open_position.

def _stage_skeleton() -> list[dict]:
    return [{"stage": name, "status": "waiting", "reason": None, "at": None} for name in STAGE_ORDER]


def _is_historical_attempt(attempt, last_decision_at_iso: str | None) -> bool:
    """True when the last real order attempt predates the CURRENT signal
    cycle - i.e. no new attempt has happened since the signal now on
    screen was decided, so its outcome (including a stale rate-limit
    rejection from minutes/hours ago) must never be shown as today's
    active "Execution Failed" (Debug 1.pdf section 3/8-9). Was previously
    hardcoded to `execution_attempted` (always true whenever any attempt
    row existed, regardless of age) - a placeholder that never actually
    distinguished old from current, which is exactly why an old
    rate-limit failure kept reappearing as a fresh one."""
    if attempt is None or attempt.created_at is None or not last_decision_at_iso:
        return False
    last_decision_at = datetime.fromisoformat(last_decision_at_iso)
    attempt_at = attempt.created_at
    if attempt_at.tzinfo is None:
        attempt_at = attempt_at.replace(tzinfo=timezone.utc)
    return attempt_at < last_decision_at


@router.get("/binance/execution-pipeline")
async def binance_execution_pipeline(symbol: str | None = None, db: Session = Depends(get_db)):
    """The current signal's decision-engine verdict overlaid with the
    ACTUAL outcome of the most recent real order attempt for this symbol -
    so "signal approved" is never shown next to a silently failed
    execution. `signal_approved` mirrors what the decision engine currently
    says; `execution_ok` is ground truth from the last real attempt, which
    may be older than the current signal (there may not have been a new
    attempt yet this cycle) - `last_attempt_at` disambiguates."""
    sym = (symbol or settings.default_symbol).upper()
    decision = await binance_decision_status(symbol=sym, db=db)

    attempt = (
        db.query(BinanceExecutionAttempt)
        .filter(BinanceExecutionAttempt.symbol == sym, BinanceExecutionAttempt.is_test.is_(False))
        .order_by(BinanceExecutionAttempt.id.desc())
        .first()
    )

    signal_approved = bool(decision.get("risk_gate_allowed"))

    # Debug 1.pdf section 8-9: Binance cooling down right now is a
    # completely different fact from "the last execution attempt failed" -
    # the frontend needs both, separately, so a cooldown with no fresh
    # attempt this cycle renders as a small "showing cached data" notice
    # instead of ever being conflated with (or hidden behind) the
    # execution-failed banner above.
    from app.exchanges.binance_rate_limiter import limiter
    rl_status = limiter.status()

    if attempt:
        stages_by_name = {s.get("stage"): s for s in (attempt.stages or [])}
        stages = [stages_by_name.get(name) or {"stage": name, "status": "waiting", "reason": None, "at": None}
                  for name in STAGE_ORDER]
        execution_attempted = True
        execution_ok = attempt.final_status == "order_accepted"
        final_reason = attempt.final_reason
        last_attempt_at = attempt.created_at.isoformat() if attempt.created_at else None
    else:
        stages = _stage_skeleton()
        execution_attempted = False
        execution_ok = None
        final_reason = None
        last_attempt_at = None

    return {
        "symbol": sym,
        "active_mode": decision.get("active_mode"),
        "signal_approved": signal_approved,
        "execution_attempted": execution_attempted,
        "execution_ok": execution_ok,
        "final_reason": final_reason,
        "stages": stages,
        "last_attempt_at": last_attempt_at,
        # Phase 30, sections 7-8: computed fresh (see binance_decision_status
        # above) - never derived from `attempt`, which is historical and may
        # be arbitrarily old. The frontend must show this separately from
        # final_reason/stages so an old protection failure never reads as
        # today's active blocker.
        "current_protection_check": decision.get("protection_check"),
        "is_historical_attempt": _is_historical_attempt(attempt, decision.get("last_decision_at")),
        "binance_rate_limited": rl_status["rate_limited"],
        "binance_retry_after_seconds": rl_status["retry_after_seconds"],
        "order_id": attempt.order_id if attempt else None,
        "requested_notional": attempt.requested_notional if attempt else None,
        "requested_quantity": attempt.requested_quantity if attempt else None,
        "adjusted_quantity": attempt.adjusted_quantity if attempt else None,
        "leverage": attempt.leverage if attempt else None,
        "margin_required": attempt.margin_required if attempt else None,
        "latency_ms": attempt.latency_ms if attempt else None,
    }


@router.get("/binance/execution-logs")
async def binance_execution_logs(limit: int = 100, db: Session = Depends(get_db)):
    """Last N real-order attempts (Binance live/testnet only - paper never
    reaches BinanceExecutionProvider), full stage-by-stage detail included,
    for the Execution Pipeline card's collapsible log panel."""
    limit = max(1, min(limit, 100))
    rows = (
        db.query(BinanceExecutionAttempt)
        .order_by(BinanceExecutionAttempt.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "attempts": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "mode": r.mode,
                "symbol": r.symbol,
                "side": r.side,
                "is_test": r.is_test,
                "confidence": r.confidence,
                "requested_notional": r.requested_notional,
                "requested_quantity": r.requested_quantity,
                "adjusted_quantity": r.adjusted_quantity,
                "leverage": r.leverage,
                "margin_required": r.margin_required,
                "final_status": r.final_status,
                "final_reason": r.final_reason,
                "exchange_response": r.exchange_response,
                "order_id": r.order_id,
                "latency_ms": r.latency_ms,
                "stages": r.stages,
            }
            for r in rows
        ],
    }


class TestOrderRequest(BaseModel):
    symbol: str | None = None
    confirm: bool = False


@router.post("/binance/test-order")
async def binance_test_order(body: TestOrderRequest, db: Session = Depends(get_db)):
    """Legacy diagnostic entry is disabled by mandatory Horizon authority.

    Keep confirmation and allowlist validation for a precise operator error,
    but never read the exchange or route an entry from this endpoint.
    """
    _require_live(db)
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation required to place a real test order (confirm=true)")

    symbol = (body.symbol or settings.default_symbol).upper()
    if symbol not in settings.binance_allowed_symbols:
        raise HTTPException(status_code=400, detail=f"{symbol} is not in BINANCE_ALLOWED_SYMBOLS")
    # A diagnostic endpoint cannot invent Active Drive V2 authority. It is
    # deliberately blocked before any exchange read or order operation.
    raise HTTPException(status_code=409, detail="HORIZON_AUTHORITY_REQUIRED")


# ============================================ TP/SL position protection repair
#
# Phase 27, section 1: immediate repair path for an existing real Binance
# position that has no TP/SL (or stale/partial protection). Unlike the
# BINANCE_REQUIRE_TP_SL gate (which only governs NEW entries), this
# endpoint exists specifically to fix a position that is already open and
# naked - it works regardless of that flag's value.

class ProtectPositionRequest(BaseModel):
    take_profit: float
    stop_loss: float
    mode: str = "full_position"


@router.post("/binance/positions/{symbol}/protect")
async def binance_protect_position(symbol: str, body: ProtectPositionRequest, db: Session = Depends(get_db)):
    """Reads the real Binance position for `symbol`, validates TP/SL
    direction against its current mark price, cancels any stale protective
    orders, places fresh reduce-only/close-position TP and SL, then
    verifies both actually exist in Binance's own open orders before
    returning - the same ground-truth verification the auto-protection
    path in BinanceExecutionProvider.open_position uses."""
    if body.mode != "full_position":
        raise HTTPException(status_code=400, detail=f"Unsupported protection mode: {body.mode!r} (only 'full_position' is implemented)")

    symbol = symbol.upper()
    mode_name = modes.effective_mode()
    client = execution_router.provider().client
    try:
        positions = await client.get_positions(symbol)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read Binance position: {_safe_error(e)}")
    if not positions:
        raise HTTPException(status_code=404, detail=f"No open {symbol} position on Binance")
    pos = positions[0]

    try:
        validate_risk_levels(pos.side, pos.mark_price, body.stop_loss, body.take_profit)
    except RiskValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    # mode=full_position always replaces both legs cleanly rather than
    # layering a new order on top of a stale one - classic resting orders
    # AND any tracked algo id (Phase 26 Algo Order Provider; algo orders
    # never show up in get_open_orders).
    try:
        for o in await client.get_open_orders(symbol):
            if o.symbol == symbol and (o.reduce_only or o.close_position):
                await client.cancel_order(symbol, o.order_id)
        for algo_id in _tracked_algo_id(mode_name, symbol, "tp"), _tracked_algo_id(mode_name, symbol, "sl"):
            if algo_id:
                try:
                    await protection_provider.cancel_leg(client, symbol, algo_id, protection_provider.ALGO)
                except Exception:
                    pass  # already gone - not a reason to block the repair
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not clear stale protective orders: {_safe_error(e)}")

    try:
        hedge_mode = await client.get_position_mode()
    except Exception:
        hedge_mode = False

    # Places TP+SL through whichever provider this mode needs (classic or
    # algo), auto-migrating and persisting the capability on the first
    # -4120 exactly like a fresh entry's protection - see
    # app.trading.protection_provider.
    result = await protection_provider.place_protection(
        client=client, mode=mode_name, symbol=symbol, position_side=pos.side,
        tp=body.take_profit, sl=body.stop_loss, quantity=pos.quantity, hedge_mode=hedge_mode,
    )
    errors: list[str] = []
    if not result.tp.ok:
        errors.append(f"take_profit: {result.tp.error}")
    if not result.sl.ok:
        errors.append(f"stop_loss: {result.sl.error}")

    tp_algo_id = result.tp.order_id if result.provider_used == protection_provider.ALGO else None
    sl_algo_id = result.sl.order_id if result.provider_used == protection_provider.ALGO else None
    tp_order_id = result.tp.order_id if result.provider_used == protection_provider.CLASSIC else None
    sl_order_id = result.sl.order_id if result.provider_used == protection_provider.CLASSIC else None

    try:
        verified = await protection.resolve_protection(
            client, symbol, tp_algo_id=tp_algo_id, sl_algo_id=sl_algo_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Protective orders were attempted but could not be verified: {_safe_error(e)}")

    modes.audit(
        "position_protected" if verified.status == protection.PROTECTED else "position_protect_failed",
        symbol=symbol,
        detail={"take_profit": body.take_profit, "stop_loss": body.stop_loss, "errors": errors,
                "status": verified.status, "provider": result.provider_used},
    )

    await execution_router.sync_positions()
    from app.trading.execution_router import _store_protection_metadata
    verified_at = datetime.now(timezone.utc).isoformat()
    _store_protection_metadata(mode_name, symbol, result.provider_used, tp_order_id, sl_order_id, tp_algo_id, sl_algo_id,
                               protection_status=verified.status)

    # Phase 30, section 5: the mutation response carries the freshly
    # verified protection state directly so the frontend can update
    # immediately instead of waiting for the next poll - _store_
    # protection_metadata above already invalidated the shared snapshot/
    # algo caches, so the one background refresh that follows will agree.
    return {
        "symbol": symbol,
        "side": pos.side,
        "entry_price": pos.entry_price,
        "mark_price": pos.mark_price,
        "quantity": pos.quantity,
        "errors": errors,
        "protection_provider": result.provider_used,
        "provider_result": result.to_dict(),
        "position": {
            "symbol": symbol, "side": pos.side, "entry_price": pos.entry_price,
            "mark_price": pos.mark_price, "quantity": pos.quantity,
        },
        "protection": {
            "status": verified.status,
            "provider": result.provider_used,
            "take_profit": verified.tp_price,
            "stop_loss": verified.sl_price,
            "verified": verified.status == protection.PROTECTED,
            "verified_at": verified_at,
        },
        "snapshot_invalidated": True,
        **verified.to_dict(),
    }


# ==================================================== protection diagnostics
#
# Phase 30: one safe, read-only view of exactly what every consumer of
# protection status (Risk Gate, watchdog, portfolio positions, Binance Real
# page) actually sees right now - built from the SAME shared resolver
# (protection.resolve_protection) and the SAME shared Binance snapshot cache
# every one of those already reads through, so this can never disagree with
# them. Added to investigate/confirm a real live position's protection
# status is being reported consistently everywhere.

@router.get("/binance/protection-status")
async def binance_protection_status(db: Session = Depends(get_db)):
    """For every real open position: the resolved protection verdict (Phase
    26 Algo-aware, Phase 30 revision-tracked), where that verdict came from,
    and how stale the underlying Binance read is. Never exposes keys,
    secrets or raw signed requests - every field here is already safe by
    construction from the modules it's assembled from."""
    from app.api.portfolio import get_account_snapshot, get_read_client
    from app.db.models import ExchangePositionRow
    from app.exchanges.binance_snapshot_service import snapshot_service

    if not modes.binance_configured():
        return {"positions": []}

    client = get_read_client()
    try:
        _, positions, _ = await get_account_snapshot(client)
        open_orders = await snapshot_service.get_open_orders(client)
    except Exception as e:
        return {"positions": [], "error": _safe_error(e)}

    meta = snapshot_service.section_meta()
    stale = bool(meta["account"]["stale"] or meta["orders"]["stale"])
    age = max(filter(None, [meta["account"]["age_seconds"], meta["orders"]["age_seconds"]]), default=None)

    mode = modes.effective_mode(db)
    rows = {
        row.symbol: row
        for row in db.query(ExchangePositionRow).filter(ExchangePositionRow.mode == mode).all()
    }

    result = []
    for pos in positions:
        row = rows.get(pos.symbol)
        verified = await protection.resolve_protection(
            client, pos.symbol, open_orders=open_orders,
            tp_algo_id=row.tp_algo_id if row else None, sl_algo_id=row.sl_algo_id if row else None,
        )
        source = (
            "tracked_algo_and_verified_lookup" if (row and (row.tp_algo_id or row.sl_algo_id))
            else "classic_open_orders_scan"
        )
        result.append({
            "symbol": pos.symbol,
            "status": verified.status,
            "provider": (row.protection_provider if row else None) or "classic",
            "has_tp": verified.tp_price is not None,
            "has_sl": verified.sl_price is not None,
            "tp": verified.tp_price,
            "sl": verified.sl_price,
            "source": source,
            "snapshot_age_seconds": age,
            "stale": stale,
            "protection_revision": row.protection_revision if row else None,
            "verified_at": row.protection_verified_at.isoformat() if row and row.protection_verified_at else None,
        })
    return {"positions": result}


# ================================================== Live Margin Calculator
#
# Phase 26: institutional-style pre-trade risk math, computed entirely
# server-side (app.trading.margin_calculator's pure, unit-tested functions)
# over live Binance account state, real exchange filters, real commission
# rates and real leverage brackets - never a guessed constant. Advisory
# only: app.trading.real_risk_gate remains the sole authority over whether
# a real order is actually allowed; nothing here can place or block one.

# Read-only evaluation candidates for the symbol-recommendation scan -
# deliberately NOT auto-added to settings.binance_allowed_symbols (that
# stays an explicit admin decision via Server Trading Control).
CANDIDATE_RECOMMENDATION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

FALLBACK_TAKER_FEE_RATE = 0.0005  # Binance USDT-M futures standard taker rate, used only if the signed read fails


async def _margin_snapshot(symbol: str, db: Session) -> dict:
    """One live Binance read (account/positions/filters/commission/
    brackets) fully reduced into every number the Live Margin Calculator
    card needs. Shared by the main endpoint and the symbol-recommendation
    scan. Never raises - a failure comes back as {"available": False}."""
    from app.api.portfolio import get_account_snapshot, get_read_client

    symbol = symbol.upper()
    if not modes.binance_configured():
        return {"available": False, "reason": "Binance API keys are not configured", "symbol": symbol}

    client = get_read_client()
    try:
        account, positions, daily_pnl = await get_account_snapshot(client)
        mark_price = await client.get_mark_price(symbol)
        filters = await client.get_exchange_filters(symbol)
    except Exception as e:
        return {"available": False, "reason": _safe_error(e), "symbol": symbol}

    # Commission/brackets are best-effort: a transient failure degrades to
    # a documented fallback rather than blocking the whole calculator (this
    # is advisory math, never the actual order gate, which Binance itself
    # still enforces exactly).
    try:
        taker_fee_rate = (await client.get_commission_rate(symbol))["taker_rate"]
    except Exception:
        taker_fee_rate = FALLBACK_TAKER_FEE_RATE

    try:
        brackets = await client.get_leverage_brackets(symbol)
    except Exception:
        brackets = []

    position = next((p for p in positions if p.symbol == symbol), None)
    available_margin = account.available_balance
    risk_settings = settings_repository.get_settings(scope="binance_real", db=db)
    safety_buffer_usdt = risk_settings["safety_buffer_usdt"]
    safety_buffer_pct = risk_settings["safety_buffer_pct"]

    configured_leverage = min(settings.binance_default_leverage, settings.binance_max_leverage)
    effective_leverage = position.leverage if position else configured_leverage

    maint_ratio, maint_cum, brackets_available = mc.maintenance_margin_for_notional(
        brackets, settings.binance_max_notional_per_trade
    )

    # ---- position sizing: hard exchange ceiling vs. the safe recommendation ----
    max_qty, max_notional_raw = mc.max_tradable_quantity(
        available_margin=available_margin, price=mark_price, leverage=settings.binance_max_leverage,
        step_size=filters["step_size"], precision=filters["quantity_precision"], min_qty=filters["min_qty"],
    )
    aggressive_breakdown = mc.compute_margin_breakdown(
        notional=max_notional_raw, leverage=settings.binance_max_leverage, available_margin=available_margin,
        taker_fee_rate=taker_fee_rate, maint_margin_ratio=maint_ratio, maint_cum=maint_cum,
        safety_buffer_usdt=0.0, safety_buffer_pct=0.0,
    ).to_dict()

    recommended_notional, reserved_buffer = mc.recommended_max_notional(
        available_margin=available_margin, leverage=configured_leverage, taker_fee_rate=taker_fee_rate,
        maint_margin_ratio=maint_ratio, safety_buffer_usdt=safety_buffer_usdt, safety_buffer_pct=safety_buffer_pct,
    )
    recommended_qty = mc.round_qty(
        recommended_notional / mark_price if mark_price else 0.0, filters["step_size"], filters["quantity_precision"]
    )
    recommended_notional = round(recommended_qty * mark_price, 2) if recommended_qty > 0 else 0.0

    current_setting_notional = settings.binance_max_notional_per_trade
    current_breakdown = mc.compute_margin_breakdown(
        notional=current_setting_notional, leverage=configured_leverage, available_margin=available_margin,
        taker_fee_rate=taker_fee_rate, maint_margin_ratio=maint_ratio, maint_cum=maint_cum,
        safety_buffer_usdt=safety_buffer_usdt, safety_buffer_pct=safety_buffer_pct,
    )

    if recommended_notional <= 0 or current_setting_notional > recommended_notional * 1.02:
        recommendation_status = "TOO_HIGH"
    elif current_setting_notional < recommended_notional * 0.5:
        recommendation_status = "TOO_LOW"
    else:
        recommendation_status = "OK"

    # ---- risk capacity gauge ----
    wallet = account.total_wallet_balance
    available_margin_pct = min(100.0, (available_margin / wallet * 100)) if wallet > 0 else 0.0
    exposure_pct = min(100.0, (sum(p.notional for p in positions) / max(wallet, 1e-9)) * 100)
    nearest_liq_pct = None
    for p in positions:
        if p.liquidation_price and p.mark_price:
            dist = abs(p.mark_price - p.liquidation_price) / p.mark_price * 100
            nearest_liq_pct = dist if nearest_liq_pct is None else min(nearest_liq_pct, dist)
    liquidation_capacity = min(100.0, nearest_liq_pct * 5) if nearest_liq_pct is not None else None
    daily_loss_capacity = None
    if settings.binance_max_daily_loss_usdt > 0:
        loss_used_pct = max(0.0, -daily_pnl) / settings.binance_max_daily_loss_usdt * 100
        daily_loss_capacity = max(0.0, 100.0 - loss_used_pct)
    open_positions_capacity = max(0.0, 100.0 - (len(positions) / max(risk_settings["max_open_positions"], 1)) * 100)

    score, level = mc.risk_capacity_score({
        "available_margin_pct": available_margin_pct,
        "exposure_headroom_pct": 100.0 - exposure_pct,
        "liquidation_distance_capacity": liquidation_capacity,
        "daily_loss_capacity": daily_loss_capacity,
        "open_positions_capacity": open_positions_capacity,
    })

    return {
        "available": True,
        "symbol": symbol,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "account": {
            "wallet_balance": round(wallet, 2),
            "available_balance": round(available_margin, 2),
            "margin_balance": round(account.total_margin_balance, 2),
            "margin_used": round(account.total_initial_margin, 2),
            "free_margin": round(available_margin, 2),
            "unrealized_pnl": round(account.total_unrealized_pnl, 2),
            "maintenance_margin_used": round(account.total_maintenance_margin, 2),
            "margin_mode": position.margin_type if position else "N/A",
            "current_leverage": effective_leverage,
            "configured_leverage": configured_leverage,
            "maximum_buying_power": round(available_margin * settings.binance_max_leverage, 2),
            "liquidation_buffer_pct": round(nearest_liq_pct, 2) if nearest_liq_pct is not None else None,
        },
        "market": {
            "current_price": mark_price,
            "min_qty": filters["min_qty"],
            "step_size": filters["step_size"],
            "quantity_precision": filters["quantity_precision"],
            "min_notional": filters["min_notional"],
            "tick_size": filters["tick_size"],
            "price_precision": filters["price_precision"],
            "taker_fee_rate": taker_fee_rate,
            "maintenance_margin_ratio": maint_ratio,
            "maintenance_margin_estimated": not brackets_available,
        },
        "position_sizing": {
            "maximum_tradable_quantity": max_qty,
            "maximum_tradable_notional": max_notional_raw,
            "required_margin_at_max": aggressive_breakdown["initial_margin"],
            "estimated_fees_at_max": aggressive_breakdown["estimated_fees"],
            "safety_buffer": round(reserved_buffer, 2),
            "maximum_safe_position_notional": recommended_notional,
            "maximum_safe_position_quantity": recommended_qty,
            "maximum_aggressive_position_notional": max_notional_raw,
        },
        "recommendation": {
            "recommended_max_notional": recommended_notional,
            "current_setting_notional": current_setting_notional,
            "current_setting_leverage": configured_leverage,
            "status": recommendation_status,
            "adjust_by": round(recommended_notional - current_setting_notional, 2),
        },
        "breakdown_at_current_setting": current_breakdown.to_dict(),
        "risk_capacity": {
            "score": score,
            "level": level,
            "components": {
                "available_margin_pct": round(available_margin_pct, 1),
                "exposure_headroom_pct": round(100.0 - exposure_pct, 1),
                "liquidation_distance_capacity": round(liquidation_capacity, 1) if liquidation_capacity is not None else None,
                "daily_loss_capacity": round(daily_loss_capacity, 1) if daily_loss_capacity is not None else None,
                "open_positions_capacity": round(open_positions_capacity, 1),
            },
        },
    }


def _build_recommendations(*, snapshot: dict, risk_settings: dict, symbol: str) -> list[dict]:
    """Section 6: plain-language, server-computed suggestions - every
    number here comes from the same margin math as the rest of the
    snapshot, never invented client-side (requirement: all calculations
    stay server-side)."""
    account = snapshot["account"]
    market = snapshot["market"]
    rec = snapshot["recommendation"]
    breakdown = snapshot["breakdown_at_current_setting"]
    available_margin = account["available_balance"]
    recommendations: list[dict] = []

    current_leverage = account["configured_leverage"]
    max_leverage = settings.binance_max_leverage
    if current_leverage < max_leverage and breakdown["notional"] > 0:
        alt = mc.compute_margin_breakdown(
            notional=breakdown["notional"], leverage=max_leverage, available_margin=available_margin,
            taker_fee_rate=market["taker_fee_rate"], maint_margin_ratio=market["maintenance_margin_ratio"],
            maint_cum=0.0, safety_buffer_usdt=risk_settings["safety_buffer_usdt"],
            safety_buffer_pct=risk_settings["safety_buffer_pct"],
        )
        saved = breakdown["total_required"] - alt.total_required
        if saved > 0.01:
            recommendations.append({
                "type": "increase_leverage",
                "message": (
                    f"Increase leverage to {max_leverage:g}x to reduce required margin by ${saved:.2f} "
                    f"for the same ${breakdown['notional']:.2f} position."
                ),
                "impact_usdt": round(saved, 2),
            })

    if breakdown["status"] == "FAIL":
        recommendations.append({
            "type": "deposit_funds",
            "message": (
                f"Deposit ${breakdown['missing']:.2f} more to unlock your current "
                f"${breakdown['notional']:.2f} position size setting."
            ),
            "impact_usdt": breakdown["missing"],
        })
    elif rec["status"] == "TOO_LOW" and rec["adjust_by"] > 1:
        recommendations.append({
            "type": "raise_setting",
            "message": (
                f"Your wallet safely supports up to ${rec['recommended_max_notional']:.2f} - your current "
                f"setting (${rec['current_setting_notional']:.2f}) is leaving capacity unused."
            ),
            "impact_usdt": round(rec["adjust_by"], 2),
        })
    elif rec["status"] == "TOO_HIGH" and rec["adjust_by"] < -0.01:
        recommendations.append({
            "type": "reduce_setting",
            "message": (
                f"Your current max position size (${rec['current_setting_notional']:.2f}) exceeds what your "
                f"wallet safely supports. Reduce by ${abs(rec['adjust_by']):.2f} to ${rec['recommended_max_notional']:.2f}, "
                "or use Auto Configure Risk."
            ),
            "impact_usdt": round(abs(rec["adjust_by"]), 2),
        })

    if rec["recommended_max_notional"] <= 0 or market["min_notional"] > rec["recommended_max_notional"]:
        recommendations.append({
            "type": "symbol_infeasible",
            "message": (
                f"Current {symbol} minimum notional (${market['min_notional']:.2f}) exceeds what your wallet can "
                "safely support right now. See Trading Recommendations for a feasible alternative symbol."
            ),
            "impact_usdt": None,
        })

    return recommendations


def _build_simulation(*, sym: str, decision: dict, snapshot: dict, safety_buffer_usdt: float,
                      safety_buffer_pct: float) -> tuple[dict | None, dict | None]:
    """Pre-trade simulation + the professional status checklist, both
    derived from the current live decision overlaid on the margin
    snapshot. Returns (simulation, status_checklist) - both None when there
    is no live decision for this exact symbol to simulate."""
    if not decision or decision.get("status") == "NO_DATA" or decision.get("symbol") != sym:
        return None, None

    market = snapshot["market"]
    price = market["current_price"]
    intended_notional = decision.get("intended_notional") or 0.0
    intended_leverage = decision.get("intended_leverage") or snapshot["account"]["configured_leverage"]

    qty = mc.round_qty(intended_notional / price if price else 0.0, market["step_size"], market["quantity_precision"])
    breakdown = mc.compute_margin_breakdown(
        notional=intended_notional, leverage=intended_leverage, available_margin=snapshot["account"]["available_balance"],
        taker_fee_rate=market["taker_fee_rate"], maint_margin_ratio=market["maintenance_margin_ratio"], maint_cum=0.0,
        safety_buffer_usdt=safety_buffer_usdt, safety_buffer_pct=safety_buffer_pct,
    )

    direction = decision.get("final_direction")
    side = direction if direction in ("LONG", "SHORT") else (decision.get("strategy_direction") or "LONG")
    take_profit = decision.get("take_profit")
    stop_loss = decision.get("stop_loss")
    expected_profit = abs(take_profit - price) * qty if take_profit and qty else None
    expected_loss = abs(price - stop_loss) * qty if stop_loss and qty else None
    liquidation_price = mc.estimate_liquidation_price(
        entry_price=price, leverage=intended_leverage, maint_margin_ratio=market["maintenance_margin_ratio"], side=side,
    )
    confidence = decision.get("confidence")
    expected_reward_pct = (expected_profit / breakdown.initial_margin * 100) if expected_profit and breakdown.initial_margin else None
    expected_risk_pct = (expected_loss / breakdown.initial_margin * 100) if expected_loss and breakdown.initial_margin else None

    simulation = {
        "symbol": sym,
        "side": side,
        "entry_price": price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "expected_profit": round(expected_profit, 2) if expected_profit is not None else None,
        "expected_loss": round(expected_loss, 2) if expected_loss is not None else None,
        "fees": round(breakdown.estimated_fees, 4),
        "liquidation_price": round(liquidation_price, market["price_precision"]),
        "required_margin": round(breakdown.initial_margin, 2),
        "probability": confidence,
        "confidence": confidence,
        "active_model": decision.get("active_model"),
        "active_strategy": decision.get("active_strategy"),
        "market_regime": decision.get("market_regime"),
        "expected_risk_pct": round(expected_risk_pct, 2) if expected_risk_pct is not None else None,
        "expected_reward_pct": round(expected_reward_pct, 2) if expected_reward_pct is not None else None,
        "optimal_quantity": qty,
        "optimal_leverage": intended_leverage,
        "optimal_margin": round(breakdown.initial_margin, 2),
    }

    quantity_valid = qty > 0 and qty >= market["min_qty"]
    margin_approved = breakdown.status == "PASS"
    model_approved = bool(decision.get("active_model"))
    risk_gate_approved = bool(decision.get("risk_gate_allowed"))
    order_ready = model_approved and risk_gate_approved and margin_approved and quantity_valid

    blocked_reason = None
    if not order_ready:
        if not model_approved:
            blocked_reason = "Model has not produced an actionable signal"
        elif not risk_gate_approved:
            blocked_reason = decision.get("risk_gate_reason")
        elif not quantity_valid:
            blocked_reason = "Quantity below minimum"
        elif not margin_approved:
            blocked_reason = f"Insufficient margin - need ${breakdown.missing:.2f} more"

    status_checklist = {
        "model_approved": model_approved,
        "risk_gate_approved": risk_gate_approved,
        "margin_approved": margin_approved,
        "quantity_valid": quantity_valid,
        "exchange_filters_passed": True,  # we reached here on real, successfully-fetched filters
        "binance_validation_passed": None,  # only knowable once an order is actually attempted
        "order_ready": order_ready,
        "overall": "READY" if order_ready else "BLOCKED",
        "blocked_reason": blocked_reason,
        "missing_amount": round(breakdown.missing, 2) if breakdown.missing > 0 else None,
    }
    return simulation, status_checklist


@router.get("/binance/margin-calculator")
async def binance_margin_calculator(symbol: str | None = None, db: Session = Depends(get_db)):
    """Live Margin Calculator: wallet/margin breakdown, position-size
    ceilings, the safe recommended max notional, a never-hidden margin
    breakdown at the currently configured setting, a risk-capacity gauge,
    and (when a live decision exists for this symbol) a full pre-trade
    simulation with the professional PASS/FAIL status checklist."""
    sym = (symbol or settings.default_symbol).upper()
    snapshot = await _margin_snapshot(sym, db)
    if not snapshot.get("available"):
        return snapshot

    decision = await binance_decision_status(symbol=sym, db=db)
    risk_settings = settings_repository.get_settings(scope="binance_real", db=db)
    simulation, status_checklist = _build_simulation(
        sym=sym, decision=decision, snapshot=snapshot,
        safety_buffer_usdt=risk_settings["safety_buffer_usdt"], safety_buffer_pct=risk_settings["safety_buffer_pct"],
    )
    snapshot["simulation"] = simulation
    snapshot["status_checklist"] = status_checklist
    snapshot["recommendations"] = _build_recommendations(snapshot=snapshot, risk_settings=risk_settings, symbol=sym)
    return snapshot


@router.get("/binance/symbol-recommendations")
async def binance_symbol_recommendations(db: Session = Depends(get_db)):
    """Section 10: when the active symbol can't trade at the current
    wallet size, which of the other liquid majors could - ranked by lowest
    real exchange-minimum margin requirement. Read-only: never adds a
    symbol to settings.binance_allowed_symbols, that stays an explicit
    admin decision (Server Trading Control)."""
    candidates = []
    for sym in CANDIDATE_RECOMMENDATION_SYMBOLS:
        snapshot = await _margin_snapshot(sym, db)
        if not snapshot.get("available"):
            continue
        market = snapshot["market"]
        min_margin = market["min_notional"] / max(snapshot["account"]["configured_leverage"], 1.0)
        candidates.append({
            "symbol": sym,
            "allowed": sym in settings.binance_allowed_symbols,
            "current_price": market["current_price"],
            "min_notional": market["min_notional"],
            "min_quantity": market["min_qty"],
            "minimum_margin_required": round(min_margin, 2),
            "recommended_max_notional": snapshot["recommendation"]["recommended_max_notional"],
            "feasible": snapshot["account"]["available_balance"] >= min_margin,
        })

    if not candidates:
        return {"available": False, "reason": "Could not evaluate any candidate symbol", "candidates": []}

    feasible = [c for c in candidates if c["feasible"] and c["allowed"]]
    best = min(feasible, key=lambda c: c["minimum_margin_required"]) if feasible else None

    return {
        "available": True,
        "candidates": sorted(candidates, key=lambda c: c["minimum_margin_required"]),
        "best_symbol": best["symbol"] if best else None,
        "reason": (
            f"Lowest minimum margin (${best['minimum_margin_required']:.2f}) among your allowed symbols"
            if best else "No allowed symbol is currently feasible at this wallet size"
        ),
    }


@router.get("/binance/protection-watchdog")
async def binance_protection_watchdog_status():
    """The protection watchdog's last scan (app.trading.protection_watchdog) -
    lets the dashboard show the safety-alert banner (section 14) without a
    dedicated signed Binance round trip of its own."""
    from app.trading.protection_watchdog import LAST_SCAN, WATCHDOG_INTERVAL_SECONDS
    return {
        **LAST_SCAN,
        "enabled": settings.binance_protection_watchdog_enabled,
        "auto_protect": settings.binance_auto_protect_positions,
        "block_new_trades_if_unprotected": settings.binance_block_new_trades_if_unprotected,
        "interval_seconds": WATCHDOG_INTERVAL_SECONDS,
    }


# ======================================================= Trading Diagnostics
#
# Phase 28: read-only investigation surface for the -4120 finding (see
# app.trading.diagnostics for the full writeup and documentation evidence).
# Nothing here places a real order except test_stop_market_order, which
# uses Binance's own no-op validation endpoint and never reaches the
# matching engine.

@router.get("/binance/diagnostics")
async def binance_trading_diagnostics(symbol: str | None = None, db: Session = Depends(get_db)):
    """Everything needed to confirm or refute the -4120 finding for this
    account: account type/mode/permissions, what /fapi/v1/order claims to
    support vs. what actually works, whether the Algo Order API is
    reachable, and the most recent real protective-order attempt. Every
    field is either read live from Binance or pulled from our own stored
    attempt history - never guessed. Degrades field-by-field (never the
    whole response) when Binance is unreachable, e.g. rate-limited."""
    from app.trading.diagnostics import (
        build_account_diagnostics,
        build_order_type_diagnostics,
        build_position_mode_diagnostics,
        build_protection_provider_diagnostics,
        last_protective_attempts,
    )
    from app.exchanges.binance_rate_limiter import limiter
    from app.exchanges.binance_snapshot_service import snapshot_service
    from app.trading.protection_watchdog import WATCHDOG_INTERVAL_SECONDS

    if not modes.binance_configured():
        return {"available": False, "reason": "Binance API keys are not configured"}

    sym = (symbol or settings.default_symbol).upper()
    client = execution_router.provider().client

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account": await build_account_diagnostics(client),
        "order_types": await build_order_type_diagnostics(client, sym),
        "protection_provider": build_protection_provider_diagnostics(modes.effective_mode(), db),
        "last_protective_attempt": last_protective_attempts(db),
        # Debug 2.pdf section 11
        "position_mode": await build_position_mode_diagnostics(client),
        "rate_limit": {
            **limiter.status(),
            "snapshot_sections": snapshot_service.section_meta(),
            "watchdog_interval_seconds": WATCHDOG_INTERVAL_SECONDS,
        },
        "config": {
            "require_tp_sl": settings.binance_require_tp_sl,
            "close_if_protection_fails": settings.binance_close_if_protection_fails,
            "watchdog_enabled": settings.binance_protection_watchdog_enabled,
        },
    }


class TestStopOrderRequest(BaseModel):
    symbol: str | None = None
    side: str = "SELL"
    stop_price: float | None = None
    quantity: float | None = None


@router.post("/binance/diagnostics/test-stop-order")
async def binance_diagnostics_test_stop_order(body: TestStopOrderRequest, db: Session = Depends(get_db)):
    """Captures the EXACT real request/response for a STOP_MARKET order
    using Binance's own documented /fapi/v1/order/test endpoint - "Testing
    order request, this order will not be submitted to matching engine"
    (Binance docs). Never places, risks, or leaves behind anything.
    Requires live trading to already be unlocked (this reuses the live
    client instance; it does not itself grant any new capability).

    stop_price/quantity default to real current mark price and the
    exchange's minimum quantity when omitted, so the diagnostic isolates
    the order-TYPE rejection (-4120) rather than risking a different,
    unrelated rejection (precision/notional) from an arbitrary guess."""
    _require_live(db)
    symbol = (body.symbol or settings.default_symbol).upper()
    client = execution_router.provider().client

    stop_price = body.stop_price
    quantity = body.quantity
    if stop_price is None or quantity is None:
        try:
            mark = await client.get_mark_price(symbol)
            filters = await client.get_exchange_filters(symbol)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not read live price/filters to build a valid test order: {_safe_error(e)}")
        if stop_price is None:
            # 2% below mark - a plausible SELL-side stop, direction doesn't
            # matter to /order/test (it never checks against a live position)
            stop_price = round(mark * 0.98, filters["price_precision"])
        if quantity is None:
            quantity = max(filters["min_qty"], mc.round_qty(filters["min_notional"] / mark, filters["step_size"], filters["quantity_precision"]))

    result = await client.test_stop_market_order(symbol, body.side, stop_price, quantity)
    modes.audit("diagnostics_test_stop_order", symbol=symbol, detail={"ok": result["ok"], "code": result.get("code")})
    return {"symbol": symbol, "endpoint": "POST /fapi/v1/order/test", **result}
