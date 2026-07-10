"""Unified portfolio view across trading modes (Phase 22).

PAPER mode reads the local simulated ledger; BINANCE_TESTNET / BINANCE_LIVE
read live from Binance through the execution router's provider (Binance is
the source of truth for real positions). Every response carries the mode it
describes so the UI can label it honestly. Nothing here ever returns API
keys, secrets or signatures.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import app.api.paper as paper_api
from app.core.config import settings
from app.db.models import ExchangePositionRow, TradingAuditLog
from app.db.session import get_db
from app.risk import settings_repository
from app.trading import modes
from app.trading.execution_router import router as execution_router

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _real_provider_or_502():
    provider = execution_router.provider()
    if getattr(provider, "mode", modes.MODE_PAPER) not in modes.REAL_MODES:
        raise HTTPException(status_code=409, detail="Not in a Binance trading mode")
    if not provider.client.configured:
        raise HTTPException(status_code=502, detail="Binance API keys are not configured")
    return provider


def _risk_summary(db: Session) -> dict:
    risk = settings_repository.get_settings(db=db)
    control = modes.get_control(db)
    mode = modes.effective_mode(db)
    return {
        "mode": mode,
        "risk_mode": "paper" if mode == modes.MODE_PAPER else ("testnet" if mode == modes.MODE_TESTNET else "live"),
        "max_daily_loss_pct": risk["max_daily_loss_pct"],
        "max_daily_loss_usdt": settings.binance_max_daily_loss_usdt,
        "max_open_positions": risk["max_open_positions"],
        "min_confidence_to_trade": risk["min_confidence_to_trade"],
        "max_leverage": settings.binance_max_leverage,
        "max_notional_per_trade": settings.binance_max_notional_per_trade,
        "allowed_symbols": settings.binance_allowed_symbols,
        "live_enabled": settings.binance_live_enabled,
        "live_unlocked": control["live_unlocked"],
        "live_lock_status": "UNLOCKED" if mode == modes.MODE_LIVE else (
            "LOCKED" if control["mode"] == modes.MODE_LIVE else "NOT_REQUESTED"
        ),
        "kill_switch_active": control["kill_switch_active"],
        "kill_switch_reason": control["kill_switch_reason"],
    }


@router.get("/summary")
async def summary(db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)

    if mode in modes.REAL_MODES:
        provider = _real_provider_or_502()
        try:
            account = await provider.client.get_account_info()
            positions = await provider.client.get_positions()
            daily_pnl = await provider.client.get_daily_realized_pnl()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Binance account unavailable: {type(e).__name__}")

        total_notional = sum(p.notional for p in positions)
        margin_used = account.total_initial_margin
        nearest_liq = None
        for p in positions:
            if p.liquidation_price and p.mark_price:
                dist = abs(p.mark_price - p.liquidation_price) / p.mark_price * 100
                nearest_liq = dist if nearest_liq is None else min(nearest_liq, dist)

        return {
            "mode": mode,
            "total_wallet_balance": round(account.total_wallet_balance, 2),
            "available_balance": round(account.available_balance, 2),
            "margin_balance": round(account.total_margin_balance, 2),
            "unrealized_pnl": round(account.total_unrealized_pnl, 2),
            "realized_pnl": None,  # lifetime realized PnL is not a single Binance field
            "daily_pnl": round(daily_pnl, 2),
            "margin_used": round(margin_used, 2),
            "free_margin": round(account.available_balance, 2),
            "open_positions": len(positions),
            "total_notional_exposure": round(total_notional, 2),
            "nearest_liquidation_distance_pct": round(nearest_liq, 2) if nearest_liq is not None else None,
            "risk": _risk_summary(db),
        }

    paper = await paper_api.portfolio(db=db)
    return {
        "mode": mode,
        "total_wallet_balance": paper["balance"],
        "available_balance": paper["available_margin"],
        "margin_balance": paper["equity"],
        "unrealized_pnl": paper["unrealized_pnl"],
        "realized_pnl": paper["total_pnl"],
        "daily_pnl": paper["daily_pnl"],
        "margin_used": paper["total_margin_used"],
        "free_margin": paper["available_margin"],
        "open_positions": paper["open_positions"],
        "total_notional_exposure": paper["total_notional_exposure"],
        "nearest_liquidation_distance_pct": paper["nearest_liquidation_distance_pct"],
        "win_rate": paper["win_rate"],
        "wins": paper["wins"],
        "losses": paper["losses"],
        "risk": _risk_summary(db),
    }


@router.get("/balances")
async def balances(db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)

    if mode in modes.REAL_MODES:
        provider = _real_provider_or_502()
        try:
            rows = await provider.client.get_balances()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Binance balances unavailable: {type(e).__name__}")
        interesting = [b for b in rows if b.balance or b.available or b.asset in ("USDT", "BTC", "ETH")]
        return {
            "mode": mode,
            "balances": [
                {
                    "asset": b.asset,
                    "available": round(b.available, 8),
                    "locked": round(b.locked, 8),
                    "total": round(b.balance, 8),
                }
                for b in interesting
            ],
        }

    paper = await paper_api.portfolio(db=db)
    return {
        "mode": mode,
        "balances": [
            {
                "asset": "USDT",
                "available": paper["available_margin"],
                "locked": paper["total_margin_used"],
                "total": paper["balance"],
            }
        ],
    }


@router.get("/positions")
async def positions(db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)

    if mode in modes.REAL_MODES:
        provider = _real_provider_or_502()
        try:
            live = await provider.client.get_positions()
            open_orders = await provider.client.get_open_orders()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Binance positions unavailable: {type(e).__name__}")

        def _protective(symbol: str, order_type: str) -> float | None:
            for o in open_orders:
                if o.symbol == symbol and o.type == order_type:
                    return o.stop_price
            return None

        # local mirror row ids let the UI target PATCH
        # /api/trading/positions/{id}/risk for real TP/SL edits
        local_ids = {
            row.symbol: row.id
            for row in db.query(ExchangePositionRow).filter(ExchangePositionRow.mode == mode).all()
        }

        return {
            "mode": mode,
            "positions": [
                {
                    **p.to_dict(),
                    "id": local_ids.get(p.symbol),
                    "sl": _protective(p.symbol, "STOP_MARKET"),
                    "tp": _protective(p.symbol, "TAKE_PROFIT_MARKET"),
                }
                for p in live
            ],
        }

    paper = await paper_api.positions(db=db)
    return {"mode": mode, "positions": paper["positions"]}


@router.get("/orders")
async def orders(db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)

    if mode in modes.REAL_MODES:
        provider = _real_provider_or_502()
        try:
            open_orders = await provider.client.get_open_orders()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Binance orders unavailable: {type(e).__name__}")
        return {"mode": mode, "orders": [o.to_dict() for o in open_orders]}

    # the paper engine fills immediately - there are never resting orders
    return {"mode": mode, "orders": []}


@router.get("/trades")
async def trades(db: Session = Depends(get_db)):
    mode = modes.effective_mode(db)

    if mode in modes.REAL_MODES:
        provider = _real_provider_or_502()
        result = []
        for symbol in settings.binance_allowed_symbols:
            try:
                for t in await provider.client.get_trade_history(symbol, limit=50):
                    result.append(t.to_dict())
            except Exception:
                continue  # per-symbol failure must not empty the whole view
        result.sort(key=lambda t: t.get("time") or 0, reverse=True)
        return {"mode": mode, "trades": result[:100]}

    paper = await paper_api.history(db=db)
    return {"mode": mode, "trades": paper["trades"]}


@router.get("/audit")
async def audit_log(limit: int = 100, db: Session = Depends(get_db)):
    """Persisted real-trading audit trail (mode changes, orders, kill
    switch, TP/SL edits). Complements the in-memory /api/logs stream."""
    limit = max(1, min(limit, 500))
    rows = db.query(TradingAuditLog).order_by(TradingAuditLog.id.desc()).limit(limit).all()
    return {
        "events": [
            {
                "id": r.id,
                "event": r.event,
                "mode": r.mode,
                "symbol": r.symbol,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
