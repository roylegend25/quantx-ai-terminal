"""Unified current Decision/Execution Pipeline API.

Single authoritative source for "what is the current decision doing right
now" - built from app.trading_horizon.current_authority (which timeframe
is authoritative) and app.trading_horizon.diagnostics (pure derivation
over persisted tables). Deliberately separate from
GET /api/trading/binance/decision-status and .../execution-pipeline
(those remain the Binance-Live-specific real-attempt transparency views);
this endpoint is mode-agnostic and is what the Dashboard's Execution
Pipeline card should read regardless of active mode."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.trading import modes
from app.trading_horizon.current_authority import resolve_authoritative_timeframe
from app.trading_horizon.diagnostics import (
    STATE_APPROVED_FOR_EXECUTION,
    STATE_APPROVED_FOR_PAPER_EXECUTION,
    STATE_AUTHORITY_BLOCKED,
    STATE_CONFIDENCE_BLOCKED,
    STATE_EDGE_BLOCKED,
    STATE_EVALUATING,
    STATE_EXECUTION_FAILED,
    STATE_EXECUTION_PENDING,
    STATE_EXPIRED,
    STATE_NO_TRADE,
    STATE_PAPER_POSITION_OPEN,
    STATE_POSITION_OPEN,
    STATE_STALE,
    STATE_TRADE_LEVELS_PENDING,
    current_pipeline_snapshot,
    derive_pipeline_state,
)

router = APIRouter(prefix="/api/trading/pipeline", tags=["trading-pipeline"])

# Canonical linear progression for a fully-approved LONG/SHORT decision.
# Blocked/terminal states report the prefix of this list actually reached.
_STAGE_PROGRESSION = [
    "confidence_passed",
    "trade_levels_validated",
    "edge_passed",
    "authority_granted",
    "risk_approved",
    "execution_requested",
    "order_submitted",
    "order_accepted",
    "position_opened",
]

# state -> index into _STAGE_PROGRESSION of the LAST stage completed
# (-1 means none of the linear stages have completed yet).
_STATE_STAGE_INDEX = {
    STATE_EVALUATING: -1,
    STATE_NO_TRADE: -1,
    STATE_CONFIDENCE_BLOCKED: -1,
    STATE_TRADE_LEVELS_PENDING: 0,
    STATE_EDGE_BLOCKED: 1,
    STATE_AUTHORITY_BLOCKED: 2,
    STATE_STALE: 3,
    STATE_EXPIRED: 3,
    STATE_APPROVED_FOR_PAPER_EXECUTION: 4,
    STATE_APPROVED_FOR_EXECUTION: 4,
    STATE_EXECUTION_PENDING: 6,
    STATE_EXECUTION_FAILED: 6,
    STATE_PAPER_POSITION_OPEN: 8,
    STATE_POSITION_OPEN: 8,
}

_BLOCKED_STATES = {
    STATE_NO_TRADE, STATE_CONFIDENCE_BLOCKED, STATE_TRADE_LEVELS_PENDING, STATE_EDGE_BLOCKED,
    STATE_AUTHORITY_BLOCKED, STATE_STALE, STATE_EXPIRED, STATE_EXECUTION_FAILED,
}


def _stages(state: str) -> tuple[list[str], str | None]:
    idx = _STATE_STAGE_INDEX.get(state, -1)
    completed = _STAGE_PROGRESSION[: idx + 1]
    pending = _STAGE_PROGRESSION[idx + 1] if idx + 1 < len(_STAGE_PROGRESSION) else None
    return completed, pending


@router.get("/current")
async def current_pipeline(symbol: str, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    user_id = settings.admin_username
    resolution = await resolve_authoritative_timeframe(db, user_id, symbol)
    timeframe = resolution["execution_timeframe"]
    snapshot = current_pipeline_snapshot(db, user_id=user_id, symbol=symbol, timeframe=timeframe)
    state, reason = derive_pipeline_state(snapshot)

    decision = snapshot["decision"]
    authority = snapshot["authority"]
    execution_intent = snapshot["execution_intent"]
    order = snapshot["order"]
    payload = (decision.decision_payload or {}) if decision is not None else {}

    candidate_direction = decision.signal if decision is not None and decision.signal in ("LONG", "SHORT") else None
    actionable = bool(decision is not None and candidate_direction is not None and decision.eligible_for_execution)
    confidence_passed = (
        None if decision is None
        else state not in (STATE_NO_TRADE, STATE_CONFIDENCE_BLOCKED, STATE_EVALUATING)
    )
    trade_levels_valid = (
        None if decision is None else bool(payload.get("recommended_stop") and payload.get("recommended_target"))
    )

    completed_stages, pending_stage = _stages(state)
    final_block_reason = reason if state in _BLOCKED_STATES else None

    order_id = None
    position_id = None
    execution_mode = None
    if order is not None:
        execution_mode = getattr(order, "execution_mode", None)
        if snapshot["effective_mode"] == modes.MODE_PAPER:
            order_id = order.id
            position_id = order.id if order.status == "OPEN" else None
        else:
            order_id = getattr(order, "order_id", None)

    timestamps = [t for t in (
        decision.created_at if decision is not None else None,
        authority.created_at if authority is not None else None,
        execution_intent.completed_at if execution_intent is not None else None,
        getattr(order, "updated_at", None) if order is not None else None,
    ) if t is not None]
    updated_at = max(timestamps) if timestamps else None

    next_evaluation_at = None
    stale = False
    if decision is not None and decision.created_at is not None:
        created = decision.created_at if decision.created_at.tzinfo else decision.created_at.replace(tzinfo=timezone.utc)
        interval = timedelta(seconds=settings.scheduler_interval_seconds)
        next_evaluation_at = (created + interval).isoformat()
        stale = datetime.now(timezone.utc) - created > interval * 3

    return {
        "decision_id": decision.decision_id if decision is not None else None,
        "cycle_id": getattr(order, "cycle_id", None) if order is not None else None,
        "authority_id": authority.id if authority is not None else None,
        "execution_request_id": execution_intent.id if execution_intent is not None else None,
        "order_id": order_id,
        "position_id": position_id,
        "engine": "active_drive_v2",
        "symbol": symbol,
        "timeframe": timeframe,
        "timeframe_source": resolution["source"],
        "signal": decision.signal if decision is not None else "NO_TRADE",
        "candidate_direction": candidate_direction,
        "actionable": actionable,
        "confidence": decision.confidence if decision is not None else None,
        "confidence_threshold": payload.get("required_confidence"),
        "confidence_passed": confidence_passed,
        "trade_levels_valid": trade_levels_valid,
        "edge_supported": decision.edge_supported if decision is not None else None,
        "expected_edge": decision.expected_edge if decision is not None else None,
        "edge_block_reason": decision.edge_block_reason if decision is not None else None,
        "authority_status": (
            "not_issued" if authority is None
            else "expired" if state in (STATE_STALE, STATE_EXPIRED)
            else "blocked" if state == STATE_AUTHORITY_BLOCKED
            else "granted"
        ),
        "authority_block_reason": reason if state == STATE_AUTHORITY_BLOCKED else None,
        "risk_allowed": True if authority is not None else None,
        "risk_reason": None,
        "execution_mode": execution_mode or ("automatic" if authority is not None else None),
        "execution_status": state,
        "current_stage": state,
        "completed_stages": completed_stages,
        "pending_stage": pending_stage,
        "final_block_reason": final_block_reason,
        "created_at": decision.created_at.isoformat() if decision is not None and decision.created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "next_evaluation_at": next_evaluation_at,
        "stale": stale,
    }
