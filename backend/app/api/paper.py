from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from pydantic import BaseModel
import json
import logging
import httpx

from app.core.security import decode_access_token
from app.db.session import get_db
from app.db.models import Trade, Portfolio
from app.strategy.performance_repository import repository as performance_repository
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository
from app.strategy import weight_calculator
from app.ml.feature_store import store as feature_store
from app.monitoring.metrics import PAPER_TRADES_CLOSED, PAPER_TRADES_OPENED
from app.monitoring.logging import get_logger, log_event
from app.risk import settings_repository
from app.trading import margin as margin_calc
from app.trading.position_manager import should_close_position

router = APIRouter(prefix="/api/paper", tags=["paper"])

logger = get_logger("quantx.paper")

# Canonical close-reason codes persisted on Trade.close_reason. Older rows
# keep their historical free-text values ("manual_close", "auto_close: SL
# hit") - history is never rewritten.
CLOSE_REASONS = {
    "TAKE_PROFIT",
    "STOP_LOSS",
    "LIQUIDATION",
    "MANUAL",
    "TRAILING_STOP",
    "MAX_HOLD_TIME",
    "RISK_ENGINE",
    "PARTIAL_CLOSE",
}


async def get_optional_user(authorization: str | None = Header(default=None)) -> str | None:
    """Identity of the caller when a valid bearer token is present.

    Authentication itself is enforced at the router level in app/main.py
    (every /api/paper route already sits behind get_current_user); this
    only extracts WHO the caller is for position-ownership bookkeeping,
    and stays optional so unit tests can mount the bare router."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return decode_access_token(authorization.removeprefix("Bearer ").strip())


def _risk_error(exc: margin_calc.RiskValidationError) -> JSONResponse:
    """Structured validation failure: {"detail": ..., "field": ...}."""
    return JSONResponse(status_code=400, content={"detail": exc.message, "field": exc.field})


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

def _margin_fields(t: Trade, mark: float | None) -> dict:
    """Leverage/margin/liquidation view of one trade. Values are derived
    only from recorded inputs; anything unknowable for older rows is null
    with an explanation in field_notes - never a fabricated number."""
    entry = t.entry
    qty = t.qty or 0.0
    notes: dict[str, str] = {}

    notional = round(qty * mark, 2) if mark else (round(qty * entry, 2) if entry else None)
    entry_notional = round(qty * entry, 2) if entry else None

    leverage = t.leverage
    margin_used = t.margin_used
    if leverage is None:
        notes["leverage"] = "not recorded when this position was opened"
    if margin_used is None:
        notes["margin_used"] = "no leverage/margin recorded at open"

    mmr = t.maintenance_margin_rate if t.maintenance_margin_rate is not None else margin_calc.DEFAULT_MAINTENANCE_MARGIN_RATE
    # Recomputed from stored inputs each read (entry/leverage/mmr are fixed
    # at open, so this is stable) rather than trusting a possibly-stale
    # stored value.
    liq = margin_calc.estimated_liquidation_price(t.side, entry, leverage, mmr)
    if liq is None:
        if leverage is None:
            notes["liquidation_price"] = "cannot estimate: leverage not recorded"
        elif leverage <= 1 and t.side == "LONG":
            notes["liquidation_price"] = "No estimated liquidation at 1x"

    maintenance_margin = round(notional * mmr, 2) if notional is not None and leverage is not None else None
    effective_leverage = round(notional / margin_used, 2) if margin_used and notional else None

    # a CLOSED row has no unrealized PnL - its result lives in pnl/realized_pnl
    pnl = margin_calc.unrealized_pnl(t.side, entry, mark, qty) if mark and entry and t.status == "OPEN" else None
    if pnl is not None:
        # ROE on committed margin when known; for pre-leverage rows the whole
        # entry notional was the committed size, so that is the honest base.
        base = margin_used if margin_used else entry_notional
        pnl_pct = round(pnl / base * 100, 2) if base else None
    else:
        pnl_pct = None

    return {
        "leverage": leverage,
        "margin_mode": t.margin_mode or ("isolated" if leverage is not None else None),
        "notional_value": notional,
        "initial_margin": round(margin_used, 2) if margin_used is not None else None,
        "margin_used": round(margin_used, 2) if margin_used is not None else None,
        "maintenance_margin": maintenance_margin,
        "maintenance_margin_rate": mmr if leverage is not None else None,
        "effective_leverage": effective_leverage,
        "liquidation_price": round(liq, 2) if liq is not None else None,
        "liquidation_label": "Estimated liquidation price" if liq is not None else None,
        "distance_to_liquidation_pct": (
            round(margin_calc.distance_to_liquidation_pct(t.side, mark, liq), 2)
            if mark and liq is not None
            else None
        ),
        "risk_reward_ratio": (
            round(margin_calc.risk_reward_ratio(t.side, entry, t.sl, t.tp), 2)
            if margin_calc.risk_reward_ratio(t.side, entry, t.sl, t.tp) is not None
            else None
        ),
        "unrealized_pnl": round(pnl, 2) if pnl is not None else None,
        "unrealized_pnl_pct": pnl_pct,
        "trailing_stop": t.trailing_stop,
        "realized_pnl": round(t.realized_pnl, 2) if t.realized_pnl is not None else None,
        "field_notes": notes or None,
    }


def _position_payload(t: Trade, mark: float) -> dict:
    """Full open-position row for GET /positions and the PATCH …/risk echo."""
    pnl = margin_calc.unrealized_pnl(t.side, t.entry, mark, t.qty)
    return {
        "id": t.id,
        "symbol": t.symbol,
        "side": t.side,
        "entry": round(t.entry, 2),
        "entry_price": round(t.entry, 2),
        "mark": round(mark, 2),
        "mark_price": round(mark, 2),
        "qty": round(t.qty, 6),
        "quantity": round(t.qty, 6),
        "pnl": round(pnl, 2),
        "sl": t.sl,
        "tp": t.tp,
        "stop_loss": t.sl,
        "take_profit": t.tp,
        "status": t.status,
        "opened_at": _iso_utc(t.opened_at),
        "updated_at": _iso_utc(t.updated_at),
        **_margin_fields(t, mark),
        **_decision_fields(t),
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
    total_margin_used = 0.0
    total_notional = 0.0
    nearest_liq_pct: float | None = None
    prices: dict[str, float] = {}
    for t in open_trades:
        if t.symbol not in prices:
            prices[t.symbol] = await get_price(t.symbol)
        price = prices[t.symbol]
        unrealized += margin_calc.unrealized_pnl(t.side, t.entry, price, t.qty)
        total_notional += t.qty * price
        # Pre-leverage rows committed their full entry notional (1x by
        # construction), so that IS their margin - not a fabricated value.
        total_margin_used += t.margin_used if t.margin_used is not None else t.entry * t.qty
        mmr = t.maintenance_margin_rate if t.maintenance_margin_rate is not None else margin_calc.DEFAULT_MAINTENANCE_MARGIN_RATE
        liq = margin_calc.estimated_liquidation_price(t.side, t.entry, t.leverage, mmr)
        dist = margin_calc.distance_to_liquidation_pct(t.side, price, liq)
        if dist is not None and (nearest_liq_pct is None or dist < nearest_liq_pct):
            nearest_liq_pct = dist

    p.equity = p.balance + unrealized
    db.commit()

    risk_settings = settings_repository.get_settings()
    equity = p.equity
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
        "max_open_positions": risk_settings["max_open_positions"],
        "total_margin_used": round(total_margin_used, 2),
        "available_margin": round(max(0.0, equity - total_margin_used), 2),
        "margin_usage_pct": round(total_margin_used / equity * 100, 2) if equity > 0 else None,
        "total_notional_exposure": round(total_notional, 2),
        # distance (percent of mark) to the closest ESTIMATED liquidation
        # among open positions; null when no position has a meaningful one
        "nearest_liquidation_distance_pct": round(nearest_liq_pct, 2) if nearest_liq_pct is not None else None,
    }

@router.post("/open")
async def open_trade(
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    usdt_size: float = 1000,
    sl: float | None = None,
    tp: float | None = None,
    leverage: float = 1.0,
    feature_id: int | None = None,
    entry_price: float | None = None,
    context: StrategyContext | None = None,
    user: str | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    symbol = symbol.upper()
    side = side.upper()

    # The emergency kill switch halts EVERY open path - the bot's gates
    # (risk_manager, execution engine, router) all check it, and this
    # covers the one remaining way in: a direct manual API/UI open.
    from app.trading import modes as trading_modes
    if trading_modes.kill_switch_active():
        raise HTTPException(status_code=423, detail="Kill switch active - all trading halted")

    if side not in ["LONG", "SHORT"]:
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")
    if not usdt_size or usdt_size <= 0:
        raise HTTPException(status_code=400, detail="usdt_size must be positive")
    if leverage < margin_calc.MIN_LEVERAGE or leverage > margin_calc.MAX_LEVERAGE:
        raise HTTPException(
            status_code=400,
            detail=f"leverage must be between {margin_calc.MIN_LEVERAGE:g}x and {margin_calc.MAX_LEVERAGE:g}x",
        )

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

    # SL/TP must at least sit on the correct side of the entry (no minimum
    # distance at open - automated strategies may legitimately place tight
    # stops; the live-edit endpoint is stricter).
    try:
        margin_calc.validate_risk_levels(side, price, sl, tp, None, min_distance_pct=0.0)
    except margin_calc.RiskValidationError as exc:
        return _risk_error(exc)

    mmr = margin_calc.DEFAULT_MAINTENANCE_MARGIN_RATE
    now = datetime.now(timezone.utc)
    trade = Trade(
        symbol=symbol,
        side=side,
        entry=price,
        qty=qty,
        status="OPEN",
        sl=sl,
        tp=tp,
        pnl=0.0,
        user_id=user,
        leverage=leverage,
        margin_mode="isolated",
        # usdt_size is the position notional; the margin actually committed
        # shrinks with leverage
        margin_used=usdt_size / leverage,
        maintenance_margin_rate=mmr,
        liquidation_price=margin_calc.estimated_liquidation_price(side, price, leverage, mmr),
        realized_pnl=0.0,
        updated_at=now,
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
            "leverage": trade.leverage,
            "margin_mode": trade.margin_mode,
            "margin_used": round(trade.margin_used, 2) if trade.margin_used is not None else None,
            "liquidation_price": round(trade.liquidation_price, 2) if trade.liquidation_price is not None else None,
        },
    }

def _close_trade_core(trade: Trade, exit_price: float, db: Session, reason: str | None = None) -> dict | None:
    """Shared close logic used by both POST /api/paper/close/{id} (a manual
    or scheduler-initiated close) and the inline stop-loss/take-profit
    enforcement in GET /api/paper/positions below - so a position is
    protected the moment *anything* reads it, not only on the dedicated
    position-manager loop's own cadence. See scenario_position_manager_delayed
    in app/stress/simulator.py for the failure mode this guards against.

    Returns None when the trade was already closed by a concurrent path:
    the status flip is an atomic UPDATE … WHERE status='OPEN', so two racing
    closers (position-manager loop vs. a /positions read vs. a manual close)
    can never both book the PnL."""
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

    now = datetime.now(timezone.utc)
    claimed = (
        db.query(Trade)
        .filter(Trade.id == trade.id, Trade.status == "OPEN")
        .update(
            {
                "status": "CLOSED",
                "exit": exit_price,
                "pnl": pnl,
                "realized_pnl": (trade.realized_pnl or 0.0) + pnl,
                "closed_at": now,
                "close_reason": reason,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )
    if not claimed:
        db.rollback()
        db.refresh(trade)
        return None
    db.refresh(trade)

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
async def close_trade(
    trade_id: int,
    reason: str = Query(default="MANUAL"),
    db: Session = Depends(get_db),
):
    trade = db.get(Trade, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail="Trade is already closed")

    if reason not in CLOSE_REASONS:
        reason = "MANUAL"

    exit_price = await get_price(trade.symbol)
    result = _close_trade_core(trade, exit_price, db, reason=reason)
    if result is None:
        raise HTTPException(status_code=400, detail="Trade is already closed")

    return {
        "ok": True,
        "message": f"Trade {trade_id} closed",
        **result,
    }


def _apply_trailing_ratchet(t: Trade, mark: float, db: Session) -> bool:
    """Move the stop toward price by the trailing distance, only ever in the
    protective direction. Returns True if the SL moved (caller commits)."""
    if not t.trailing_stop or t.trailing_stop <= 0:
        return False
    if t.side == "LONG":
        candidate = mark - t.trailing_stop
        if candidate > 0 and (t.sl is None or candidate > t.sl):
            t.sl = candidate
            t.updated_at = datetime.now(timezone.utc)
            return True
    else:
        candidate = mark + t.trailing_stop
        if t.sl is None or candidate < t.sl:
            t.sl = candidate
            t.updated_at = datetime.now(timezone.utc)
            return True
    return False


@router.get("/positions")
async def positions(db: Session = Depends(get_db)):
    trades = db.query(Trade).filter(Trade.status == "OPEN").order_by(Trade.id.desc()).all()

    # Enforces SL/TP/liquidation on every read of open positions, not only
    # on the dedicated position-manager loop's own poll cadence - GET
    # /positions is hit independently and far more often (every dashboard
    # refresh), so a stalled background loop (GC pause, event-loop
    # starvation, deploy) is never the *only* thing standing between a
    # breached stop and an actual close. should_close_position() is the same
    # pure check app.trading.position_manager uses.
    result = []
    prices: dict[str, float] = {}
    for t in trades:
        if t.symbol not in prices:
            prices[t.symbol] = await get_price(t.symbol)
        price = prices[t.symbol]

        if _apply_trailing_ratchet(t, price, db):
            db.commit()

        mmr = t.maintenance_margin_rate if t.maintenance_margin_rate is not None else margin_calc.DEFAULT_MAINTENANCE_MARGIN_RATE
        liq = margin_calc.estimated_liquidation_price(t.side, t.entry, t.leverage, mmr)
        should_close, close_reason = should_close_position(
            t.side, price, t.sl, t.tp,
            liquidation_price=liq,
            trailing=bool(t.trailing_stop),
        )
        if should_close:
            # Liquidation fills at the estimated liquidation level, not the
            # (better) mark that merely crossed it - the loss booked should
            # match the event being simulated.
            exit_price = liq if close_reason == "LIQUIDATION" and liq else price
            _close_trade_core(t, exit_price, db, reason=close_reason)
            continue

        result.append(_position_payload(t, price))

    return {"positions": result}


class RiskUpdate(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None


@router.patch("/positions/{position_id}/risk")
async def update_position_risk(
    position_id: int,
    body: RiskUpdate,
    user: str | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Live SL/TP/trailing-stop edit for an OPEN paper position. Fields
    omitted from the body are left unchanged; fields sent as null are
    cleared. All values are validated server-side against the live mark
    price - the frontend's preview math is never trusted."""
    trade = db.get(Trade, position_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Position not found")
    if trade.status != "OPEN":
        return JSONResponse(status_code=400, content={"detail": "Cannot edit risk on a closed position", "field": None})
    if trade.user_id and user and trade.user_id != user:
        raise HTTPException(status_code=403, detail="You do not own this position")

    fields_set = body.model_fields_set
    new_sl = body.stop_loss if "stop_loss" in fields_set else trade.sl
    new_tp = body.take_profit if "take_profit" in fields_set else trade.tp
    new_ts = body.trailing_stop if "trailing_stop" in fields_set else trade.trailing_stop

    mark = await get_price(trade.symbol)
    try:
        margin_calc.validate_risk_levels(trade.side, mark, new_sl, new_tp, new_ts)
    except margin_calc.RiskValidationError as exc:
        return _risk_error(exc)

    trade.sl = new_sl
    trade.tp = new_tp
    trade.trailing_stop = new_ts
    trade.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trade)

    log_event(
        logger,
        message="paper_risk_updated",
        category="trading",
        trade_id=trade.id,
        symbol=trade.symbol,
        stop_loss=trade.sl,
        take_profit=trade.tp,
        trailing_stop=trade.trailing_stop,
    )

    return {"ok": True, "position": _position_payload(trade, mark)}


class CloseRequest(BaseModel):
    quantity: float | None = None


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: int,
    body: CloseRequest | None = None,
    user: str | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Full or partial close of an OPEN paper position. Omitting quantity
    (or sending >= the position size) closes everything; a smaller quantity
    realizes PnL on that slice and leaves the rest open."""
    trade = db.get(Trade, position_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Position not found")
    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail="Position is already closed")
    if trade.user_id and user and trade.user_id != user:
        raise HTTPException(status_code=403, detail="You do not own this position")

    qty = body.quantity if body else None
    if qty is not None and qty <= 0:
        return JSONResponse(status_code=400, content={"detail": "quantity must be positive", "field": "quantity"})

    exit_price = await get_price(trade.symbol)

    # Treat "basically everything" as a full close so float dust never
    # leaves an unmanageable residual position behind.
    if qty is None or qty >= trade.qty * 0.999:
        result = _close_trade_core(trade, exit_price, db, reason="MANUAL")
        if result is None:
            raise HTTPException(status_code=400, detail="Position is already closed")
        return {"ok": True, "message": f"Position {position_id} closed", "closed_quantity": round(trade.qty, 6), **result}

    # ---- partial close: realize PnL on the slice, book a CLOSED journal
    # row for it, shrink the remainder proportionally.
    pnl = margin_calc.unrealized_pnl(trade.side, trade.entry, exit_price, qty)
    now = datetime.now(timezone.utc)
    remaining_frac = (trade.qty - qty) / trade.qty

    slice_row = Trade(
        symbol=trade.symbol,
        side=trade.side,
        entry=trade.entry,
        exit=exit_price,
        qty=qty,
        status="CLOSED",
        sl=trade.sl,
        tp=trade.tp,
        pnl=pnl,
        realized_pnl=pnl,
        opened_at=trade.opened_at,
        closed_at=now,
        close_reason="PARTIAL_CLOSE",
        user_id=trade.user_id,
        leverage=trade.leverage,
        margin_mode=trade.margin_mode,
        margin_used=trade.margin_used * (1 - remaining_frac) if trade.margin_used is not None else None,
        maintenance_margin_rate=trade.maintenance_margin_rate,
        trailing_stop=trade.trailing_stop,
        regime=trade.regime,
        timeframe=trade.timeframe,
        decision_mode=trade.decision_mode,
        champion_model_id=trade.champion_model_id,
        champion_model_type=trade.champion_model_type,
        strategy_used=trade.strategy_used,
        confidence=trade.confidence,
        updated_at=now,
        # feature_id / strategy_snapshot intentionally NOT copied: outcome
        # recording and strategy-weight attribution happen once, on the
        # final close of the remaining position.
    )
    db.add(slice_row)

    trade.qty = trade.qty - qty
    if trade.margin_used is not None:
        trade.margin_used = trade.margin_used * remaining_frac
    trade.realized_pnl = (trade.realized_pnl or 0.0) + pnl
    trade.updated_at = now

    p = get_portfolio(db)
    p.balance += pnl
    p.total_pnl += pnl
    p.daily_pnl += pnl
    if pnl >= 0:
        p.wins += 1
    else:
        p.losses += 1
    db.commit()
    db.refresh(trade)

    PAPER_TRADES_CLOSED.labels(symbol=trade.symbol).inc()
    log_event(
        logger,
        message="paper_trade_partial_close",
        category="trading",
        symbol=trade.symbol,
        trade_id=trade.id,
        side=trade.side,
        closed_qty=round(qty, 6),
        remaining_qty=round(trade.qty, 6),
        exit_price=round(exit_price, 2),
        pnl=round(pnl, 2),
    )

    return {
        "ok": True,
        "message": f"Partially closed {round(qty, 6)} of position {position_id}",
        "exit": round(exit_price, 2),
        "pnl": round(pnl, 2),
        "closed_quantity": round(qty, 6),
        "remaining_quantity": round(trade.qty, 6),
        "position": _position_payload(trade, exit_price),
    }

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
                # margin view priced at exit for closed rows (entry for the
                # rare OPEN row in history) so notional/PnL% describe the
                # trade as it ended, not a live mark
                **_margin_fields(t, t.exit if t.exit else t.entry),
                **_decision_fields(t),
            }
            for t in trades
        ]
    }
