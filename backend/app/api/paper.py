from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import httpx

from app.db.session import get_db
from app.db.models import Trade, Portfolio

router = APIRouter(prefix="/api/paper", tags=["paper"])

BINANCE_FAPI = "https://fapi.binance.com"

async def get_price(symbol: str) -> float:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/price",
            params={"symbol": symbol.upper()},
        )
        r.raise_for_status()
        return float(r.json()["price"])

def get_portfolio(db: Session) -> Portfolio:
    portfolio = db.get(Portfolio, 1)
    if not portfolio:
        portfolio = Portfolio(id=1)
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
    return portfolio

@router.get("/portfolio")
async def portfolio(db: Session = Depends(get_db)):
    p = get_portfolio(db)
    open_trades = db.query(Trade).filter(Trade.status == "OPEN").all()

    unrealized = 0.0
    for t in open_trades:
        price = await get_price(t.symbol)
        if t.side == "LONG":
            unrealized += (price - t.entry) * t.qty
        else:
            unrealized += (t.entry - price) * t.qty

    p.equity = p.balance + unrealized
    db.commit()

    return {
        "balance": round(p.balance, 2),
        "equity": round(p.equity, 2),
        "unrealized_pnl": round(unrealized, 2),
        "daily_pnl": round(p.daily_pnl, 2),
        "total_pnl": round(p.total_pnl, 2),
        "wins": p.wins,
        "losses": p.losses,
        "win_rate": round((p.wins / max(1, p.wins + p.losses)) * 100, 2),
        "open_positions": len(open_trades),
    }

@router.post("/open")
async def open_trade(
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    usdt_size: float = 1000,
    sl: float | None = None,
    tp: float | None = None,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()
    side = side.upper()

    if side not in ["LONG", "SHORT"]:
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")

    price = await get_price(symbol)
    qty = usdt_size / price

    trade = Trade(
        symbol=symbol,
        side=side,
        entry=price,
        qty=qty,
        status="OPEN",
        sl=sl,
        tp=tp,
        pnl=0.0,
    )

    db.add(trade)
    db.commit()
    db.refresh(trade)

    return {
        "ok": True,
        "message": f"Paper {side} opened on {symbol}",
        "trade": {
            "id": trade.id,
            "symbol": trade.symbol,
            "side": trade.side,
            "entry": trade.entry,
            "qty": trade.qty,
            "status": trade.status,
            "sl": trade.sl,
            "tp": trade.tp,
        },
    }

@router.post("/close/{trade_id}")
async def close_trade(trade_id: int, db: Session = Depends(get_db)):
    trade = db.get(Trade, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail="Trade is already closed")

    exit_price = await get_price(trade.symbol)

    if trade.side == "LONG":
        pnl = (exit_price - trade.entry) * trade.qty
    else:
        pnl = (trade.entry - exit_price) * trade.qty

    trade.exit = exit_price
    trade.pnl = pnl
    trade.status = "CLOSED"
    trade.closed_at = datetime.now(timezone.utc)

    p = get_portfolio(db)
    p.balance += pnl
    p.total_pnl += pnl
    p.daily_pnl += pnl
    if pnl >= 0:
        p.wins += 1
    else:
        p.losses += 1

    db.commit()

    return {
        "ok": True,
        "message": f"Trade {trade_id} closed",
        "exit": round(exit_price, 2),
        "pnl": round(pnl, 2),
    }

@router.get("/positions")
async def positions(db: Session = Depends(get_db)):
    trades = db.query(Trade).filter(Trade.status == "OPEN").order_by(Trade.id.desc()).all()

    result = []
    for t in trades:
        price = await get_price(t.symbol)
        pnl = (price - t.entry) * t.qty if t.side == "LONG" else (t.entry - price) * t.qty
        result.append({
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "entry": round(t.entry, 2),
            "mark": round(price, 2),
            "qty": round(t.qty, 6),
            "pnl": round(pnl, 2),
            "sl": t.sl,
            "tp": t.tp,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        })

    return {"positions": result}

@router.get("/history")
async def history(db: Session = Depends(get_db)):
    trades = db.query(Trade).order_by(Trade.id.desc()).limit(100).all()

    return {
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "entry": round(t.entry, 2) if t.entry else None,
                "exit": round(t.exit, 2) if t.exit else None,
                "qty": round(t.qty, 6) if t.qty else None,
                "status": t.status,
                "pnl": round(t.pnl, 2) if t.pnl else 0,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in trades
        ]
    }
