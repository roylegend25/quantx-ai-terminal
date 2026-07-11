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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.bot import BOT_STATE, update_state
from app.core.config import settings
from app.db.session import get_db
from app.trading import modes
from app.trading.execution_router import router as execution_router

router = APIRouter(prefix="/api/trading", tags=["trading"])


# ================================================================== mode

@router.get("/mode")
async def get_mode(db: Session = Depends(get_db)):
    return modes.exchange_safe_status(db)


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
    confirmation: str = ""
    acknowledgements: dict[str, bool] = {}


@router.post("/binance/unlock-live")
async def unlock_live(body: LiveUnlockRequest, db: Session = Depends(get_db)):
    """The only path to BINANCE_LIVE execution. Requires the server env
    lock to be open, the exact typed phrase, and every acknowledgement."""
    if not settings.binance_live_enabled:
        raise HTTPException(
            status_code=403,
            detail="Live trading disabled by server configuration. Set BINANCE_LIVE_ENABLED=true in the backend .env only when ready.",
        )
    if not modes.binance_configured():
        raise HTTPException(status_code=400, detail="Binance API keys are not configured on the server")
    if body.confirmation.strip() != modes.LIVE_UNLOCK_PHRASE:
        raise HTTPException(status_code=400, detail=f'Type exactly "{modes.LIVE_UNLOCK_PHRASE}" to confirm')
    missing = [k for k in modes.LIVE_UNLOCK_ACKNOWLEDGEMENTS if not body.acknowledgements.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"All safety acknowledgements must be checked (missing: {', '.join(missing)})",
        )

    control = modes.unlock_live(db=db)
    return {
        "ok": True,
        "message": "LIVE trading unlocked. Real funds are now at risk.",
        "control": control,
        "status": modes.exchange_safe_status(db),
    }


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
    symbol: str
    order_id: int


@router.post("/binance/cancel-order")
async def binance_cancel_order(body: CancelOrderRequest, db: Session = Depends(get_db)):
    _require_live(db)
    result = await execution_router.cancel_order(symbol=body.symbol, order_id=body.order_id)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel failed")
    return result.to_dict()


class CancelAllRequest(BaseModel):
    symbol: str | None = None
    confirm: bool = False


@router.post("/binance/cancel-all-orders")
async def binance_cancel_all_orders(body: CancelAllRequest, db: Session = Depends(get_db)):
    _require_live(db)
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation required to cancel all real orders (confirm=true)")
    result = await execution_router.cancel_all_orders(symbol=body.symbol)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel-all failed")
    return result.to_dict()


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
