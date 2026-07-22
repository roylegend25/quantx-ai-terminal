"""One authoritative, read-only view of "what is the current decision/
execution pipeline doing right now" for a symbol - derived entirely from
existing persisted tables (ActiveDriveDecision, ExecutionIntentAudit,
Trade, BinanceBotTrade).

Trading Horizon removal: this used to also read TradingHorizonDecision (a
separate "authority" object). ActiveDriveDecision now carries its own
execution_approved/valid_until/risk_allowed/final_block_reason (see
app.decision_engine.execution_gate) - the single persisted decision IS
the authority, so there is nothing left to join against.

This is deliberately a pure function, not a new state-machine table: every
transition it reports is already a real, timestamped, reason-coded row
written by the real gates (decision engine, execution gate, execution
router). Adding a second write path here would let the "state" drift from
what the gates actually did - a derivation function cannot drift, because
it has no state of its own.

Used by GET /api/trading/pipeline/current (app.api.pipeline) and by
`binance_decision_status`'s resolved timeframe (both share the same
timeframe resolution via app.trading_horizon.current_authority)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ActiveDriveDecision, BinanceBotTrade, ExecutionIntentAudit, Trade
from app.decision_engine.repository import owner
from app.trading import modes

STATE_EVALUATING = "evaluating"
STATE_NO_TRADE = "no_trade"
STATE_CONFIDENCE_BLOCKED = "confidence_blocked"
STATE_TRADE_LEVELS_PENDING = "trade_levels_pending"
STATE_EDGE_BLOCKED = "edge_blocked"
STATE_AUTHORITY_BLOCKED = "authority_blocked"
STATE_STALE = "stale"
STATE_EXPIRED = "expired"
STATE_APPROVED_FOR_EXECUTION = "approved_for_execution"
STATE_APPROVED_FOR_PAPER_EXECUTION = "approved_for_paper_execution"
STATE_EXECUTION_PENDING = "execution_pending"
STATE_EXECUTION_FAILED = "execution_failed"
STATE_PAPER_POSITION_OPEN = "paper_position_open"
STATE_POSITION_OPEN = "position_open"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def current_pipeline_snapshot(db: Session, *, user_id: str, symbol: str, timeframe: str) -> dict:
    symbol = symbol.upper()
    normalized_user = owner(user_id)

    decision = (
        db.query(ActiveDriveDecision)
        .filter(ActiveDriveDecision.user_id == normalized_user, ActiveDriveDecision.symbol == symbol,
                ActiveDriveDecision.timeframe == timeframe, ActiveDriveDecision.shadow.is_(False))
        .order_by(ActiveDriveDecision.created_at.desc())
        .first()
    )
    execution_intent = None
    order_row = None
    if decision is not None:
        execution_intent = (
            db.query(ExecutionIntentAudit)
            .filter(ExecutionIntentAudit.profile_decision_id == decision.decision_id)
            .order_by(ExecutionIntentAudit.id.desc())
            .first()
        )
        mode = modes.effective_mode(db)
        if mode == modes.MODE_PAPER:
            order_row = (
                db.query(Trade).filter(Trade.authority_id == decision.decision_id)
                .order_by(Trade.id.desc()).first()
            )
        else:
            order_row = (
                db.query(BinanceBotTrade).filter(BinanceBotTrade.authority_id == decision.decision_id)
                .order_by(BinanceBotTrade.id.desc()).first()
            )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "user_id": normalized_user,
        "decision": decision,
        "execution_intent": execution_intent,
        "order": order_row,
        "effective_mode": modes.effective_mode(db),
    }


def derive_pipeline_state(snapshot: dict) -> tuple[str, str]:
    """Returns (state, reason). `reason` is always the exact string from the
    real gate that produced the state - never a generic fallback - except
    for the terminal states that have no single blocking reason to report."""
    decision: ActiveDriveDecision | None = snapshot.get("decision")
    if decision is None:
        return STATE_EVALUATING, "No decision has been evaluated yet for this symbol/timeframe."

    if decision.signal == "NO_TRADE":
        blockers = decision.blocking_reasons or []
        reason = blockers[0] if blockers else "The model produced no actionable direction this cycle."
        return STATE_NO_TRADE, reason

    payload = decision.decision_payload or {}
    blockers = list(decision.blocking_reasons or [])

    if not decision.eligible_for_execution or blockers:
        edge_reasons = [b for b in blockers if "edge is not supported" in b.lower()]
        level_reasons = [b for b in blockers if "risk/reward" in b.lower()]
        confidence_reasons = [b for b in blockers if b not in edge_reasons and b not in level_reasons]
        if confidence_reasons:
            return STATE_CONFIDENCE_BLOCKED, confidence_reasons[0]
        if level_reasons:
            return STATE_TRADE_LEVELS_PENDING, level_reasons[0]
        if edge_reasons:
            return STATE_EDGE_BLOCKED, edge_reasons[0]
        return STATE_CONFIDENCE_BLOCKED, "Execution gate did not pass."

    if decision.edge_supported is False:
        return STATE_EDGE_BLOCKED, decision.edge_block_reason or "Current edge is not supported."

    if not payload.get("recommended_stop") or not payload.get("recommended_target"):
        return STATE_TRADE_LEVELS_PENDING, "Trade levels (stop/target) are not available for this decision."

    now = datetime.now(timezone.utc)

    if decision.risk_allowed is False:
        return STATE_AUTHORITY_BLOCKED, decision.risk_reason or "Portfolio risk gate blocked this decision."

    if decision.execution_approved is not True:
        return STATE_AUTHORITY_BLOCKED, decision.final_block_reason or "Decision did not pass every mandatory execution gate."

    execution_intent = snapshot.get("execution_intent")
    order_row = snapshot.get("order")

    valid_until = decision.valid_until
    if valid_until is not None:
        valid_until = _aware(valid_until)
        if valid_until <= now and execution_intent is None:
            return STATE_EXPIRED, f"Decision expired at {valid_until.isoformat()} without being consumed."

    mode = snapshot.get("effective_mode")
    approved_state = STATE_APPROVED_FOR_PAPER_EXECUTION if mode == modes.MODE_PAPER else STATE_APPROVED_FOR_EXECUTION

    if execution_intent is None:
        return approved_state, "Decision approved; awaiting the next scheduler cycle to request execution."

    if execution_intent.status == "ACTIVE":
        return STATE_EXECUTION_PENDING, "Execution request is in flight."

    result = execution_intent.result or {}
    if not result.get("ok"):
        return STATE_EXECUTION_FAILED, result.get("reason") or "Execution request did not complete."

    if order_row is not None:
        if mode == modes.MODE_PAPER:
            return STATE_PAPER_POSITION_OPEN, "Paper position opened."
        return STATE_POSITION_OPEN, "Position opened."
    return STATE_EXECUTION_PENDING, "Execution accepted; order/position not yet linked."
