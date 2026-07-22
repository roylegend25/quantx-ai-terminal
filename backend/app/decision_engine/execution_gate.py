"""Post-persist execution-approval gate for Active Drive V2 decisions -
the single authoritative place where a persisted decision becomes
consumable for execution. Extracted from Trading Horizon's authority
issuance (app.trading_horizon.authority, now deprecated/historical-only)
so the same safety guarantees - portfolio risk breakers, position sizing,
a validity window, exactly-once consumption - apply directly to
ActiveDriveDecision instead of a separate authority object.

market data -> features -> Active Drive V2 evaluation -> calibrated
confidence -> trade levels -> net edge -> [HERE: risk approval] ->
persisted, execution-approved V2 decision -> paper/live safety gate ->
execution request -> order -> position -> performance update."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ActiveDriveDecision, ActiveDriveDecisionConsumption, Portfolio, Trade
from app.decision_engine.repository import owner
from app.risk import settings_repository
from app.timeframes.canonical import TIMEFRAME_CAPABILITIES
from app.trading import risk_manager


class ExecutionGateError(ValueError):
    pass


def _valid_until(timeframe: str, generated_at: datetime) -> datetime:
    """Same principled freshness bound as the fix applied to Horizon's
    authority TTL (Stage 1 performance audit root cause): at least one
    candle of the decision's own timeframe, and at least
    scheduler_interval_seconds + 30s so the scheduler's next cycle always
    lands before this decision expires - closing the gap that previously
    triggered repeated multi-timeframe re-evaluation cascades."""
    candle_ms = (TIMEFRAME_CAPABILITIES.get(timeframe) or TIMEFRAME_CAPABILITIES["5m"]).fixed_duration_ms or 0
    valid_seconds = max(candle_ms // 1000, settings.scheduler_interval_seconds + 30)
    return generated_at + timedelta(seconds=valid_seconds)


def _portfolio_risk_gate(db: Session, *, user_id: str, direction: str, confidence_pct: float,
                         open_positions: int, balance: float, daily_pnl: float) -> tuple[bool, str | None]:
    """The daily/weekly loss, drawdown, consecutive-loss and cooldown
    breakers configured on the Risk Management page
    (app.trading.risk_manager.evaluate_risk). A blocked decision here is a
    correct outcome, not a bug, whenever a configured limit is genuinely
    tripped."""
    closed_trades = (db.query(Trade).filter(Trade.status == "CLOSED", Trade.user_id == user_id)
                     .order_by(Trade.closed_at.desc()).limit(200).all())
    trade_history = [{"status": t.status, "pnl": t.pnl,
                      "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                      "closed_at": t.closed_at.isoformat() if t.closed_at else None} for t in closed_trades]
    verdict = risk_manager.evaluate_risk(
        confidence=confidence_pct, direction=direction, open_positions=open_positions,
        portfolio={"balance": balance, "daily_pnl": daily_pnl}, trade_history=trade_history,
    )
    if not verdict["allowed"]:
        return False, f"PORTFOLIO_RISK_LIMIT_BLOCKED:{verdict['reason']}"
    return True, None


def finalize_decision_for_execution(db: Session, decision_id: str) -> ActiveDriveDecision:
    """Runs immediately after ledger.persist(). For a NO_TRADE/blocked
    decision, stamps the validity window and final_block_reason only. For
    an eligible, edge-supported decision with valid trade levels,
    additionally runs the portfolio risk gate and sets execution_approved
    accordingly - the same checks Horizon's authority issuance used to
    run, now applied directly to the one persisted decision. Idempotent:
    safe to call more than once for the same decision_id."""
    row = db.get(ActiveDriveDecision, decision_id)
    if row is None:
        raise ExecutionGateError("DECISION_NOT_FOUND")
    normalized_user = owner(row.user_id)
    now = datetime.now(timezone.utc)
    generated_at = row.created_at
    if generated_at is None:
        generated_at = now
    elif generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    payload = row.decision_payload or {}
    row.valid_from = generated_at
    row.valid_until = _valid_until(row.timeframe, generated_at)
    row.confidence_threshold = payload.get("required_confidence")
    row.entry_price = payload.get("entry_price") or payload.get("reference_price")
    row.target_price = payload.get("recommended_target")
    row.stop_price = payload.get("recommended_stop")
    row.actionable = bool(row.signal in ("LONG", "SHORT") and row.eligible_for_execution)
    row.updated_at = now

    if not row.actionable:
        row.risk_allowed = None
        row.risk_reason = None
        row.execution_approved = False
        row.final_block_reason = (row.blocking_reasons or ["NO_TRADE"])[0]
        db.commit()
        return row

    if not row.edge_supported:
        row.risk_allowed = None
        row.risk_reason = None
        row.execution_approved = False
        row.final_block_reason = row.edge_block_reason or "EDGE_NOT_SUPPORTED"
        db.commit()
        return row

    if row.entry_price is None or row.target_price is None or row.stop_price is None:
        row.risk_allowed = None
        row.risk_reason = None
        row.execution_approved = False
        row.final_block_reason = "TRADE_LEVELS_UNAVAILABLE"
        db.commit()
        return row

    portfolio = db.get(Portfolio, 1)
    equity = float((portfolio.equity if portfolio and portfolio.equity is not None else
                    portfolio.balance if portfolio and portfolio.balance is not None else 10_000.0))
    balance = float(portfolio.balance if portfolio and portfolio.balance is not None else equity)
    daily_pnl = float(portfolio.daily_pnl if portfolio and portfolio.daily_pnl is not None else 0.0)
    open_trades = db.query(Trade).filter(Trade.status == "OPEN", Trade.user_id == normalized_user).all()
    raw_confidence = row.confidence or 0.0
    confidence_pct = raw_confidence * 100 if raw_confidence <= 1 else raw_confidence
    risk_allowed, risk_reason = _portfolio_risk_gate(
        db, user_id=normalized_user, direction=row.signal, confidence_pct=confidence_pct,
        open_positions=len(open_trades), balance=balance, daily_pnl=daily_pnl,
    )
    row.risk_allowed = risk_allowed
    row.risk_reason = risk_reason
    row.execution_approved = bool(risk_allowed)
    row.final_block_reason = None if risk_allowed else risk_reason
    db.commit()
    return row


def build_risk_approval_snapshot(db: Session, row: ActiveDriveDecision) -> dict:
    """Same shape app.trading_horizon.sizing.calculate_position_size()
    expects (that function is generic position-sizing math, not
    Horizon-specific, and is reused as-is), built directly from the
    persisted decision instead of a separate authority object."""
    normalized_user = owner(row.user_id)
    risk = settings_repository.get_user_settings(normalized_user, scope="paper", db=db)
    open_trades = db.query(Trade).filter(Trade.status == "OPEN", Trade.user_id == normalized_user).all()
    portfolio_exposure = sum(abs(float(t.entry or 0) * float(t.qty or 0)) for t in open_trades)
    symbol_exposure = sum(abs(float(t.entry or 0) * float(t.qty or 0)) for t in open_trades if t.symbol == row.symbol)
    portfolio = db.get(Portfolio, 1)
    equity = float((portfolio.equity if portfolio and portfolio.equity is not None else
                    portfolio.balance if portfolio and portfolio.balance is not None else 10_000.0))
    configured = float(risk["max_position_size_usd"])
    risk_pct = float(risk["max_risk_per_trade_pct"])
    stop_distance_pct = (abs(row.entry_price - row.stop_price) / row.entry_price * 100
                         if row.entry_price and row.stop_price and row.entry_price > 0 else None)
    approved_risk_budget = equity * risk_pct / 100
    stop_based = (approved_risk_budget / (stop_distance_pct / 100)
                  if stop_distance_pct and stop_distance_pct > 0 else configured)
    portfolio_remaining = max(0.0, configured * int(risk.get("max_open_positions") or 1) - portfolio_exposure)
    symbol_remaining = max(0.0, configured - symbol_exposure)
    approved = min(configured, stop_based, portfolio_remaining, symbol_remaining, max(0.0, equity))
    return {
        "risk_policy_revision": risk.get("updated_at") or "risk-settings:initial",
        "user_id": normalized_user, "symbol": row.symbol,
        "approved_notional_ceiling_usd": max(0.0, approved),
        "stop_distance_pct": stop_distance_pct,
    }


def validate_decision_for_consumption(
    db: Session, *, decision_id: str, user_id: str, symbol: str,
    direction: str | None = None, now: datetime | None = None, consume_key: str | None = None,
) -> ActiveDriveDecision:
    """Consumption-time re-validation - replaces
    trading_horizon.authority.validate_horizon_decision() (now
    deprecated/historical-only). Same guarantees: ownership/symbol/
    direction match, not expired, execution_approved, exactly-once
    consumption via a primary-key IntegrityError."""
    now = now or datetime.now(timezone.utc)
    row = db.get(ActiveDriveDecision, decision_id)
    if row is None:
        raise ExecutionGateError("DECISION_NOT_FOUND")
    if owner(row.user_id) != owner(user_id):
        raise ExecutionGateError("DECISION_USER_MISMATCH")
    if row.symbol != symbol.upper():
        raise ExecutionGateError("DECISION_SYMBOL_MISMATCH")
    if direction is not None and row.signal != direction.upper():
        raise ExecutionGateError("DECISION_DIRECTION_MISMATCH")
    if not row.execution_approved:
        raise ExecutionGateError(row.final_block_reason or "DECISION_NOT_EXECUTION_APPROVED")
    valid_until = row.valid_until
    if valid_until is not None and valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    if valid_until is not None and now >= valid_until:
        raise ExecutionGateError("DECISION_EXPIRED")
    if db.get(ActiveDriveDecisionConsumption, row.decision_id) is not None:
        raise ExecutionGateError("DECISION_ALREADY_CONSUMED")
    if consume_key is not None:
        try:
            db.add(ActiveDriveDecisionConsumption(decision_id=row.decision_id, idempotency_key=consume_key, consumed_at=now))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ExecutionGateError("DECISION_ALREADY_CONSUMED")
        except Exception:
            db.rollback()
            raise
    return row
