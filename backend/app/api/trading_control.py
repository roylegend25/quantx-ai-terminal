"""Trading mode control, order entry, live unlock and kill switch (Phase 22).

Every route here sits behind authentication (router-level dependency in
app/main.py). Real orders are only reachable through the execution router,
which enforces the real-trading risk gate internally - there is no bypass
parameter on any of these endpoints.
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


@router.get("/mode")
async def get_mode(db: Session = Depends(get_db)):
    return modes.exchange_safe_status(db)


@router.post("/enable-paper")
async def enable_paper(db: Session = Depends(get_db)):
    control = modes.set_mode(modes.MODE_PAPER, db=db)
    return {"ok": True, "message": "Paper trading mode enabled", "control": control}


@router.post("/enable-testnet")
async def enable_testnet(db: Session = Depends(get_db)):
    if not modes.binance_configured():
        raise HTTPException(status_code=400, detail="Binance API keys are not configured on the server")
    if not settings.binance_futures_testnet:
        raise HTTPException(
            status_code=400,
            detail="BINANCE_FUTURES_TESTNET is false - configure testnet keys and set it to true first",
        )
    control = modes.set_mode(modes.MODE_TESTNET, db=db)
    return {"ok": True, "message": "Binance testnet trading enabled", "control": control}


class LiveUnlockRequest(BaseModel):
    confirmation: str = ""
    acknowledgements: dict[str, bool] = {}


@router.post("/request-live-unlock")
async def request_live_unlock(body: LiveUnlockRequest, db: Session = Depends(get_db)):
    """The only path to BINANCE_LIVE. Requires the server env lock to be
    open, the exact typed phrase, and every acknowledgement checked."""
    if not settings.binance_live_enabled:
        raise HTTPException(status_code=403, detail="Live trading is disabled by server configuration.")
    if not modes.binance_configured():
        raise HTTPException(status_code=400, detail="Binance API keys are not configured on the server")
    if body.confirmation.strip() != modes.LIVE_UNLOCK_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Type exactly "{modes.LIVE_UNLOCK_PHRASE}" to confirm',
        )
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


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: str  # LONG | SHORT
    notional_usdt: float
    leverage: float | None = None
    order_type: str = "MARKET"
    price: float | None = None  # for LIMIT (real modes)
    stop_loss: float | None = None
    take_profit: float | None = None
    # Explicit human confirmation - required for any real (non-paper) order.
    confirm: bool = False


@router.post("/place-order")
async def place_order(body: PlaceOrderRequest, db: Session = Depends(get_db)):
    side = body.side.upper()
    if side not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")

    mode = modes.effective_mode(db)
    if mode in modes.REAL_MODES and not body.confirm:
        raise HTTPException(
            status_code=400,
            detail=f"Explicit confirmation required to place a real {mode} order (confirm=true)",
        )

    result = await execution_router.open_position(
        symbol=body.symbol,
        side=side,
        notional_usdt=body.notional_usdt,
        leverage=body.leverage,
        sl=body.stop_loss,
        tp=body.take_profit,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Order rejected")
    return result.to_dict()


class ClosePositionRequest(BaseModel):
    position_id: int | None = None
    symbol: str | None = None
    quantity: float | None = None
    confirm: bool = False


@router.post("/close-position")
async def close_position(body: ClosePositionRequest, db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)
    if mode in modes.REAL_MODES and not body.confirm:
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


@router.patch("/positions/{position_id}/risk")
async def update_position_risk(position_id: int, body: PositionRiskUpdate, db: Session = Depends(get_db)):
    """Edit TP/SL through the router: paper edits hit the paper ledger, real
    modes cancel-and-replace actual reduce-only orders on Binance."""
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


@router.post("/cancel-order")
async def cancel_order(body: CancelOrderRequest):
    result = await execution_router.cancel_order(symbol=body.symbol, order_id=body.order_id)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel failed")
    return result.to_dict()


class CancelAllRequest(BaseModel):
    symbol: str | None = None
    confirm: bool = False


@router.post("/cancel-all-orders")
async def cancel_all_orders(body: CancelAllRequest, db: Session = Depends(get_db)):
    if modes.effective_mode(db) in modes.REAL_MODES and not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation required to cancel all real orders (confirm=true)")
    result = await execution_router.cancel_all_orders(symbol=body.symbol)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "Cancel-all failed")
    return result.to_dict()


class KillSwitchRequest(BaseModel):
    active: bool = True
    reason: str | None = None
    # cancel resting exchange orders as part of the emergency stop
    cancel_orders: bool = True


@router.post("/kill-switch")
async def kill_switch(body: KillSwitchRequest, db: Session = Depends(get_db)):
    """Emergency stop: halts the bot, blocks ALL new trades (paper and
    real), and optionally cancels resting exchange orders. Never
    auto-closes live positions - use close-position/cancel endpoints with
    explicit confirmation for that."""
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
