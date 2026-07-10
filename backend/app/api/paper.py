from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from pydantic import BaseModel
import json
import logging
import httpx

from app.db.session import get_db
from app.db.models import Trade, Portfolio
from app.strategy.performance_repository import repository as performance_repository
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository
from app.strategy import weight_calculator
from app.ml.feature_store import store as feature_store
from app.monitoring.metrics import PAPER_TRADES_CLOSED, PAPER_TRADES_OPENED
from app.monitoring.logging import get_logger, log_event
from app.risk import settings_repository
from app.trading.position_manager import should_close_position

router = APIRouter(prefix="/api/paper", tags=["paper"])

logger = get_logger("quantx.paper")


def _iso_utc(dt: datetime | None) -> str | None:
    """Trades are timestamped with tz-aware UTC datetimes, but SQLite drops
    tzinfo on round-trip, so isoformat() would otherwise omit the offset and
    let clients misread the value as local time instead of UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class StrategyContext(BaseModel):
    regime: str | None = None
    strategies: dict | None = None
    # decision provenance from the prediction's decision_engine block -
    # absent for manual API opens, which are honestly recorded as "manual"
    timeframe: str | None = None
    decision_mode: str | None = None
    champion_model_id: str | None = None
    champion_model_type: str | None = None
    strategy_used: str | None = None
    confidence: float | None = None
    required_confidence: float | None = None
    risk_allowed: bool | None = None
    risk_reason: str | None = None
    decision_reasons: list[str] | None = None
    model_votes: list[dict] | None = None


def _decision_fields(t: Trade) -> dict:
    """Decision-provenance fields shared by /positions and /history rows."""
    return {
        "timeframe": t.timeframe,
        "decision_mode": t.decision_mode,
        "champion_model_id": t.champion_model_id,
        "champion_model_type": t.champion_model_type,
        "strategy_used": t.strategy_used,
        "confidence": t.confidence,
        "required_confidence": t.required_confidence,
        "risk_allowed": t.risk_allowed,
        "risk_reason": t.risk_reason,
        "decision_reasons": t.decision_reasons,
        "model_votes": t.model_votes,
        "regime": t.regime,
        "close_reason": t.close_reason,
        "feature_id": t.feature_id,
    }

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
    feature_id: int | None = None,
    entry_price: float | None = None,
    context: StrategyContext | None = None,
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()
    side = side.upper()

    if side not in ["LONG", "SHORT"]:
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")

    # The authoritative, atomic max_open_positions gate: TradingEngine.run_cycle()
    # and ExecutionEngine.submit_order() both pre-check this before ever
    # reaching here, but neither can make that check atomic with the trade
    # write - a second concurrent caller (a duplicate scheduler instance, a
    # multi-worker deployment, or a manual API call racing the engine) could
    # otherwise pass the same pre-check and open past the configured limit.
    # This is the one place that actually writes the row, so it's the one
    # place that can enforce the limit for real.
    risk_settings = settings_repository.get_settings()
    open_count = db.query(Trade).filter(Trade.status == "OPEN").count()
    if open_count >= risk_settings["max_open_positions"]:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum open positions reached ({risk_settings['max_open_positions']})",
        )

    # entry_price lets a caller that already simulated the fill (see
    # app/execution) book the trade at that price instead of a fresh last-
    # trade lookup, so its slippage/quality accounting stays consistent
    # with what actually got persisted here.
    price = entry_price if entry_price and entry_price > 0 else await get_price(symbol)
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
        regime=context.regime if context else None,
        strategy_snapshot=json.dumps(context.strategies) if context and context.strategies else None,
        feature_id=feature_id,
        timeframe=context.timeframe if context else None,
        # No decision context means nothing automated decided this - a
        # direct API/UI call - so label it "manual" rather than leaving the
        # journal ambiguous about who opened it.
        decision_mode=(context.decision_mode if context and context.decision_mode else "manual"),
        champion_model_id=context.champion_model_id if context else None,
        champion_model_type=context.champion_model_type if context else None,
        strategy_used=context.strategy_used if context else None,
        confidence=context.confidence if context else None,
        required_confidence=context.required_confidence if context else None,
        risk_allowed=context.risk_allowed if context else None,
        risk_reason=context.risk_reason if context else None,
        decision_reasons=context.decision_reasons if context else None,
        model_votes=context.model_votes if context else None,
    )

    db.add(trade)
    db.commit()
    db.refresh(trade)

    PAPER_TRADES_OPENED.labels(symbol=symbol, side=side).inc()
    log_event(
        logger,
        message="paper_trade_opened",
        category="trading",
        symbol=symbol,
        trade_id=trade.id,
        side=side,
        entry=round(price, 2),
    )

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

def _close_trade_core(trade: Trade, exit_price: float, db: Session, reason: str | None = None) -> dict:
    """Shared close logic used by both POST /api/paper/close/{id} (a manual
    or scheduler-initiated close) and the inline stop-loss/take-profit
    enforcement in GET /api/paper/positions below - so a position is
    protected the moment *anything* reads it, not only on the dedicated
    position-manager loop's own cadence. See scenario_position_manager_delayed
    in app/stress/simulator.py for the failure mode this guards against."""
    if trade.side == "LONG":
        pnl = (exit_price - trade.entry) * trade.qty
        price_diff = exit_price - trade.entry
    else:
        pnl = (trade.entry - exit_price) * trade.qty
        price_diff = trade.entry - exit_price

    if trade.sl is not None:
        risk_per_unit = abs(trade.entry - trade.sl)
    else:
        risk_per_unit = 0.0

    if risk_per_unit > 0:
        r_multiple = price_diff / risk_per_unit
    else:
        r_multiple = price_diff / (trade.entry * 0.01) if trade.entry else 0.0

    trade.exit = exit_price
    trade.pnl = pnl
    trade.status = "CLOSED"
    trade.closed_at = datetime.now(timezone.utc)
    trade.close_reason = reason

    p = get_portfolio(db)
    p.balance += pnl
    p.total_pnl += pnl
    p.daily_pnl += pnl
    if pnl >= 0:
        p.wins += 1
    else:
        p.losses += 1

    db.commit()

    if trade.feature_id:
        try:
            feature_store.record_outcome(
                trade.feature_id,
                exit_price=exit_price,
                pnl=pnl,
                db=db,
            )
        except Exception as e:
            log_event(
                logger,
                message="feature_store_outcome_error",
                level=logging.ERROR,
                category="trading",
                trade_id=trade.id,
                error=repr(e),
            )

    if trade.strategy_snapshot:
        snapshot = json.loads(trade.strategy_snapshot)
        for name, result in snapshot.items():
            if result.get("direction") != trade.side:
                continue
            performance_repository.update_metrics(
                name,
                r_multiple=r_multiple,
                win=pnl >= 0,
                confidence=result.get("confidence") or 0,
                regime=trade.regime,
                db=db,
            )
            rolling_metrics_repository.record_trade(
                name,
                r_multiple=r_multiple,
                win=pnl >= 0,
                confidence=result.get("confidence") or 0,
                regime=trade.regime,
                db=db,
            )
        weight_calculator.recompute_and_store(db=db)

    PAPER_TRADES_CLOSED.labels(symbol=trade.symbol).inc()
    log_event(
        logger,
        message="paper_trade_closed",
        category="trading",
        symbol=trade.symbol,
        trade_id=trade.id,
        side=trade.side,
        exit_price=round(exit_price, 2),
        pnl=round(pnl, 2),
        reason=reason,
    )

    return {"exit": round(exit_price, 2), "pnl": round(pnl, 2)}


@router.post("/close/{trade_id}")
async def close_trade(trade_id: int, db: Session = Depends(get_db)):
    trade = db.get(Trade, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail="Trade is already closed")

    exit_price = await get_price(trade.symbol)
    result = _close_trade_core(trade, exit_price, db, reason="manual_close")

    return {
        "ok": True,
        "message": f"Trade {trade_id} closed",
        **result,
    }

@router.get("/positions")
async def positions(db: Session = Depends(get_db)):
    trades = db.query(Trade).filter(Trade.status == "OPEN").order_by(Trade.id.desc()).all()

    # Enforces SL/TP on every read of open positions, not only on the
    # dedicated position-manager loop's own poll cadence - GET /positions is
    # hit independently and far more often (every dashboard refresh), so a
    # stalled background loop (GC pause, event-loop starvation, deploy) is
    # never the *only* thing standing between a breached stop and an actual
    # close. should_close_position() is the same pure check
    # app.trading.position_manager uses.
    result = []
    for t in trades:
        price = await get_price(t.symbol)
        should_close, close_reason = should_close_position(t.side, price, t.sl, t.tp)
        if should_close:
            _close_trade_core(t, price, db, reason=f"auto_close: {close_reason}")
            continue

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
            "opened_at": _iso_utc(t.opened_at),
            **_decision_fields(t),
        })

    return {"positions": result}

@router.post("/reset")
async def reset_paper_trading(db: Session = Depends(get_db)):
    """Wipes the paper-trading ledger back to a clean $10,000 start: every
    Trade row (open and closed), the singleton Portfolio row, and the
    adaptive strategy-weight stats derived from closed paper trades.

    Paper-only by construction - this touches nothing under app/exchanges
    (the real-money read path) and there is no order-placement code path
    anywhere in this codebase for reset to have to worry about disturbing.
    """
    open_count = db.query(Trade).filter(Trade.status == "OPEN").count()
    closed_count = db.query(Trade).filter(Trade.status == "CLOSED").count()

    db.query(Trade).delete()

    portfolio = get_portfolio(db)
    portfolio.balance = 10000.0
    portfolio.equity = 10000.0
    portfolio.daily_pnl = 0.0
    portfolio.total_pnl = 0.0
    portfolio.wins = 0
    portfolio.losses = 0
    db.commit()

    performance_repository.reset_all(db=db)
    rolling_metrics_repository.reset_all(db=db)

    log_event(
        logger,
        message="paper_trading_reset",
        category="trading",
        positions_closed=open_count,
        history_cleared=closed_count,
    )

    return {
        "ok": True,
        "message": "Paper trading reset successfully",
        "balance": 10000,
    }


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
                "opened_at": _iso_utc(t.opened_at),
                "closed_at": _iso_utc(t.closed_at),
                "sl": t.sl,
                "tp": t.tp,
                **_decision_fields(t),
            }
            for t in trades
        ]
    }
