"""Separate bot trade journals (Phase 23).

/api/bot/trades/paper   - rows from the paper Trade ledger, with full
                          decision provenance and margin view.
/api/bot/trades/binance - rows from binance_bot_trades, written only when
                          this system placed a REAL order Binance accepted.

The two are different tables with different lifecycles and are never
merged; a paper trade can never appear in the Binance journal or vice
versa (see test_bot_trade_separation.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import app.api.paper as paper_api
from app.db.models import BinanceBotTrade, Trade
from app.db.session import get_db

router = APIRouter(prefix="/api/bot/trades", tags=["bot-trades"])


@router.get("/paper")
async def paper_bot_trades(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.query(Trade).order_by(Trade.id.desc()).limit(limit).all()
    trades = []
    for t in rows:
        mark = t.exit if t.exit else t.entry
        trades.append({
            "id": t.id,
            "label": "MANUAL" if (t.decision_mode or "manual") == "manual" else "BOT_TRADE",
            "symbol": t.symbol,
            "side": t.side,
            "entry": round(t.entry, 2) if t.entry else None,
            "exit": round(t.exit, 2) if t.exit else None,
            "qty": round(t.qty, 6) if t.qty else None,
            "status": t.status,
            "tp": t.tp,
            "sl": t.sl,
            "realized_pnl": round(t.pnl, 2) if t.pnl else 0,
            "opened_at": paper_api._iso_utc(t.opened_at),
            "closed_at": paper_api._iso_utc(t.closed_at),
            **paper_api._margin_fields(t, mark),
            **paper_api._decision_fields(t),
        })
    return {"mode": "PAPER", "trades": trades}


@router.get("/binance")
async def binance_bot_trades(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.query(BinanceBotTrade).order_by(BinanceBotTrade.id.desc()).limit(limit).all()
    return {
        "mode": "BINANCE_LIVE",
        "trades": [
            {
                "id": r.id,
                "label": r.label or "BOT_TRADE",
                "action": r.action,
                "order_id": r.order_id,
                "client_order_id": r.client_order_id,
                "symbol": r.symbol,
                "side": r.side,
                "order_side": r.order_side,
                "order_type": r.order_type,
                "quantity": r.quantity,
                "avg_fill_price": r.avg_fill_price,
                "status": r.status,
                "reduce_only": r.reduce_only,
                "notional": r.notional,
                "leverage": r.leverage,
                "sl_order_id": r.sl_order_id,
                "tp_order_id": r.tp_order_id,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "confidence": r.confidence,
                "strategy": r.strategy,
                "model": r.model,
                "decision_reason": r.decision_reason,
                "risk_gate": r.risk_gate,
                "realized_pnl": r.realized_pnl,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
