"""Pure-function classification matrix for
app.trading_horizon.diagnostics.derive_pipeline_state - one case per state,
each asserting the reported reason is the exact string the real gate
produced, never a generic fallback. This is the direct fix for the bug
where the Execution Pipeline card showed "Model has not produced an
actionable signal" for a decision that had actually passed later gates."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ActiveDriveDecision, ExecutionIntentAudit, Portfolio, Trade, UserBotSetting
from app.db.session import SessionLocal
from app.decision_engine.execution_gate import finalize_decision_for_execution
from app.trading import modes
from app.trading_horizon.diagnostics import (
    STATE_APPROVED_FOR_PAPER_EXECUTION, STATE_AUTHORITY_BLOCKED, STATE_CONFIDENCE_BLOCKED,
    STATE_EDGE_BLOCKED, STATE_EVALUATING, STATE_EXECUTION_FAILED, STATE_EXECUTION_PENDING,
    STATE_NO_TRADE, STATE_PAPER_POSITION_OPEN, STATE_TRADE_LEVELS_PENDING,
    current_pipeline_snapshot, derive_pipeline_state,
)

from tests.test_horizon_authority import setup_user

USER = "pipeline-test-user"
SYMBOL = "BTCUSDT"
TF = "15m"


@pytest.fixture(autouse=True)
def clean_state():
    setup_user(USER)
    modes.set_mode(modes.MODE_PAPER)
    db = SessionLocal()
    db.query(ExecutionIntentAudit).delete()
    db.query(Trade).delete()
    db.commit()
    db.close()
    yield


def _decision(**overrides) -> ActiveDriveDecision:
    defaults = dict(
        decision_id=f"{USER}-{datetime.now(timezone.utc).timestamp()}", user_id=USER, engine="active_drive_v2",
        engine_version="2.2.0", symbol=SYMBOL, timeframe=TF, signal="LONG", confidence=0.8,
        expected_edge=0.02, edge_supported=True, eligible_for_execution=True, blocking_reasons=[],
        decision_payload={"recommended_stop": 100.0, "recommended_target": 120.0, "required_confidence": 0.7,
                         "reference_price": 110.0},
        shadow=False,
    )
    defaults.update(overrides)
    db = SessionLocal()
    row = ActiveDriveDecision(**defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    db.close()
    return row


def _approved_decision(**overrides) -> ActiveDriveDecision:
    """A genuinely execution_approved decision - replaces the old Horizon-
    era persisted_decision() fixture. Built directly via _decision() +
    finalize_decision_for_execution() (the single-authoritative-decision
    replacement for Trading Horizon authority issuance)."""
    row = _decision(**overrides)
    db = SessionLocal()
    try:
        finalized = finalize_decision_for_execution(db, row.decision_id)
        # Force every attribute to load while the session is still open -
        # finalize's own commit() expires them, and the caller needs a
        # usable, detached object after this session closes.
        db.refresh(finalized)
        for column in ActiveDriveDecision.__table__.columns:
            getattr(finalized, column.name)
        db.expunge(finalized)
        return finalized
    finally:
        db.close()


def _snapshot():
    db = SessionLocal()
    try:
        return current_pipeline_snapshot(db, user_id=USER, symbol=SYMBOL, timeframe=TF)
    finally:
        db.close()


def test_no_decision_yet_is_evaluating():
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_EVALUATING


def test_no_trade_signal_reports_exact_reason():
    _decision(signal="NO_TRADE", eligible_for_execution=False,
              blocking_reasons=["Calibrated directional confidence not established: relevant source history 3/50"])
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_NO_TRADE
    assert reason == "Calibrated directional confidence not established: relevant source history 3/50"


def test_candidate_long_alone_does_not_execute_confidence_blocked():
    """A LONG candidate whose confidence gate failed must never be reported
    as approved - this is the core "candidate != execution approval" rule."""
    _decision(eligible_for_execution=False,
              blocking_reasons=["Calibrated directional confidence below threshold"])
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_CONFIDENCE_BLOCKED
    assert reason == "Calibrated directional confidence below threshold"


def test_confidence_alone_passing_does_not_execute_trade_levels_pending():
    """Confidence passing (no confidence-shaped blocker) with a
    risk/reward-unavailable blocker must report trade_levels_pending, not
    a false execution approval."""
    _decision(eligible_for_execution=False, blocking_reasons=["Risk/reward is unavailable"])
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_TRADE_LEVELS_PENDING
    assert reason == "Risk/reward is unavailable"


def test_edge_not_supported_reports_exact_edge_reason():
    _decision(eligible_for_execution=False,
              blocking_reasons=["Current edge is not supported: NEGATIVE_EXPECTED_VALUE"])
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_EDGE_BLOCKED
    assert reason == "Current edge is not supported: NEGATIVE_EXPECTED_VALUE"


def test_eligible_but_missing_trade_levels_in_payload():
    _decision(decision_payload={"required_confidence": 0.7})
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_TRADE_LEVELS_PENDING


def test_eligible_with_levels_but_no_authority_is_authority_blocked():
    _decision()
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_AUTHORITY_BLOCKED


def test_fully_approved_paper_decision_reports_approved_for_paper_execution():
    _approved_decision()
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_APPROVED_FOR_PAPER_EXECUTION


def test_execution_pending_when_intent_active():
    decision = _approved_decision()
    db = SessionLocal()
    db.add(ExecutionIntentAudit(
        idempotency_key="dry-run-active", scope_key="scope", user_id=USER, symbol=SYMBOL,
        engine="active_drive_v2", profile_decision_id=decision.decision_id,
        execution_timeframe=TF, direction=decision.signal, status="ACTIVE",
    ))
    db.commit()
    db.close()
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_EXECUTION_PENDING


def test_execution_failed_reports_exact_router_reason_not_generic():
    decision = _approved_decision()
    db = SessionLocal()
    db.add(ExecutionIntentAudit(
        idempotency_key="dry-run-failed", scope_key="scope", user_id=USER, symbol=SYMBOL,
        engine="active_drive_v2", profile_decision_id=decision.decision_id,
        execution_timeframe=TF, direction=decision.signal, status="TERMINAL",
        result={"ok": False, "reason": "below_min_notional"},
    ))
    db.commit()
    db.close()
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_EXECUTION_FAILED
    assert reason == "below_min_notional"
    assert reason != "Model has not produced an actionable signal"


def test_paper_position_open_when_order_linked():
    decision = _approved_decision()
    db = SessionLocal()
    db.add(ExecutionIntentAudit(
        idempotency_key="dry-run-ok", scope_key="scope", user_id=USER, symbol=SYMBOL,
        engine="active_drive_v2", profile_decision_id=decision.decision_id,
        execution_timeframe=TF, direction=decision.signal, status="TERMINAL",
        result={"ok": True, "mode": "PAPER", "action": "open_position"},
    ))
    db.add(Trade(symbol=SYMBOL, side=decision.signal, entry=100.0, qty=1.0, status="OPEN",
                 authority_id=decision.decision_id, decision_id=decision.decision_id,
                 execution_mode="automatic", user_id=USER))
    db.commit()
    db.close()
    state, reason = derive_pipeline_state(_snapshot())
    assert state == STATE_PAPER_POSITION_OPEN


def test_historical_authority_never_leaks_into_a_newer_snapshot():
    """An old, already-consumed decision's order must never be reported as
    belonging to a brand-new decision for the same symbol/timeframe."""
    old_decision = _approved_decision()
    db = SessionLocal()
    db.add(Trade(symbol=SYMBOL, side=old_decision.signal, entry=90.0, qty=1.0, status="CLOSED",
                 authority_id=old_decision.decision_id, user_id=USER))
    db.commit()
    db.close()

    new_decision = _approved_decision()
    assert new_decision.decision_id != old_decision.decision_id
    snapshot_row = _snapshot()
    assert snapshot_row["order"] is None
    assert snapshot_row["execution_intent"] is None
    state, reason = derive_pipeline_state(snapshot_row)
    assert state == STATE_APPROVED_FOR_PAPER_EXECUTION
