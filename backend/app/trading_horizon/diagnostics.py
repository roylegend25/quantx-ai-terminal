"""One authoritative, read-only view of "what is the current decision/
execution pipeline doing right now" for a symbol - derived entirely from
existing persisted tables (ActiveDriveDecision, TradingHorizonDecision,
TradingHorizonTimeframeLink, ExecutionIntentAudit, Trade, BinanceBotTrade).

This is deliberately a pure function, not a new state-machine table: every
transition it reports is already a real, timestamped, reason-coded row
written by the real gates (decision engine, horizon authority issuance,
execution router). Adding a second write path here would let the "state"
drift from what the gates actually did - a derivation function cannot
drift, because it has no state of its own.

Used by GET /api/trading/pipeline/current (app.api.pipeline) and by
`binance_decision_status`'s resolved timeframe (both now share the same
timeframe resolution via app.trading_horizon.current_authority)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    ActiveDriveDecision,
    BinanceBotTrade,
    ExecutionIntentAudit,
    Trade,
    TradingHorizonDecision,
)
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
    authority = (
        db.query(TradingHorizonDecision)
        .filter(TradingHorizonDecision.user_id == normalized_user, TradingHorizonDecision.symbol == symbol,
                TradingHorizonDecision.authoritative_execution_timeframe == timeframe)
        .order_by(TradingHorizonDecision.generated_at.desc())
        .first()
    )
    execution_intent = None
    order_row = None
    if authority is not None:
        execution_intent = (
            db.query(ExecutionIntentAudit)
            .filter(ExecutionIntentAudit.profile_decision_id == authority.id)
            .order_by(ExecutionIntentAudit.id.desc())
            .first()
        )
        mode = modes.effective_mode(db)
        if mode == modes.MODE_PAPER:
            order_row = (
                db.query(Trade).filter(Trade.authority_id == authority.id)
                .order_by(Trade.id.desc()).first()
            )
        else:
            order_row = (
                db.query(BinanceBotTrade).filter(BinanceBotTrade.authority_id == authority.id)
                .order_by(BinanceBotTrade.id.desc()).first()
            )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "user_id": normalized_user,
        "decision": decision,
        "authority": authority,
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

    authority: TradingHorizonDecision | None = snapshot.get("authority")
    if authority is None:
        return STATE_AUTHORITY_BLOCKED, "No Trading Horizon authority has been issued for this decision yet."

    now = datetime.now(timezone.utc)
    authority_blockers = authority.blocking_reasons or []
    if authority_blockers:
        return STATE_AUTHORITY_BLOCKED, authority_blockers[0]["message"] if isinstance(authority_blockers[0], dict) else str(authority_blockers[0])
    if not authority.execution_eligible or not authority.readiness:
        return STATE_AUTHORITY_BLOCKED, "Trading Horizon authority did not pass every mandatory issuance gate."

    execution_intent = snapshot.get("execution_intent")
    order_row = snapshot.get("order")

    if _aware(authority.expires_at) <= now and execution_intent is None:
        return STATE_EXPIRED, f"Authority expired at {authority.expires_at.isoformat()} without being consumed."

    mode = snapshot.get("effective_mode")
    approved_state = STATE_APPROVED_FOR_PAPER_EXECUTION if mode == modes.MODE_PAPER else STATE_APPROVED_FOR_EXECUTION

    if execution_intent is None:
        return approved_state, "Authority granted; awaiting the next scheduler cycle to request execution."

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
