import logging
import time

from app.core.config import settings
from app.core.security import INTERNAL_SERVICE_SUBJECT
from app.execution.order_router import OrderType
from app.monitoring.logging import get_logger, log_event
from app.trading.execution_router import router as execution_router
from app.trading import modes

logger = get_logger("quantx.trading_engine")


def _blocked_outcome(reason: str, *, data_reliable: bool | None) -> str:
    text = (reason or "").lower()
    if data_reliable is False or any(word in text for word in ("data", "stale", "volume", "spread")):
        return "BLOCKED_BY_DATA"
    if any(word in text for word in ("balance", "margin", "notional", "position size")):
        return "BLOCKED_BY_BALANCE"
    if any(word in text for word in ("risk", "loss", "position", "confidence", "duplicate")):
        return "BLOCKED_BY_RISK"
    return "NO_TRADE"


def _record_candidate(symbol: str, outcome: str, reason: str, **detail) -> None:
    payload = {"outcome": outcome, "reason": reason, **detail}
    log_event(logger, message="trade_candidate_outcome", category="trading", symbol=symbol, **payload)
    modes.audit("trade_candidate_outcome", symbol=symbol, detail=payload)

class TradingEngine:

    def __init__(self):
        # Every configured symbol is evaluated every cycle (see
        # settings.symbols / ENABLED_SYMBOLS) - previously this only ever
        # traded settings.default_symbol, which is why ETHUSDT never got a
        # paper trade even though its prediction/risk pipeline worked fine.
        self.symbols = settings.symbols

    async def run_cycle(self, execution_lease_owner: str | None = None):
        for symbol in self.symbols:
            # One symbol's failure must not stop the rest of the cycle.
            try:
                await self._run_symbol_cycle(symbol, execution_lease_owner)
            except Exception as e:
                log_event(
                    logger,
                    message="scheduler_symbol_error",
                    level=logging.ERROR,
                    category="scheduler",
                    symbol=symbol,
                    error=repr(e),
                )

    async def _run_symbol_cycle(self, symbol: str, execution_lease_owner: str | None = None):
        cycle_id = f"scheduler:{int(time.time() // settings.scheduler_interval_seconds)}"
        log_event(logger, message="scheduler_cycle_started", category="scheduler", symbol=symbol, cycle_id=cycle_id)

        # Trading Horizon removal: the scheduler used to call
        # evaluate_and_issue_horizon_authority(), which ran a multi-timeframe
        # fan-out and issued a separate authority object. It now resolves
        # ONE configured execution timeframe (static profile lookup, no live
        # evaluation) and calculates Active Drive V2 exactly once for that
        # (symbol, timeframe) - the persisted decision IS the authority (see
        # app.decision_engine.execution_gate).
        from app.db.session import SessionLocal
        from app.decision_engine.profiles import resolve_execution_timeframe
        from app.decision_engine.repository import get_setting
        from app.api.prediction import compute_and_persist_prediction as active_drive_prediction

        setting_db = SessionLocal()
        try:
            setting = get_setting(setting_db, INTERNAL_SERVICE_SUBJECT)
            execution_tf = resolve_execution_timeframe(setting.trading_profile)
        finally:
            setting_db.close()

        response = await active_drive_prediction(symbol, interval=execution_tf, current_user=INTERNAL_SERVICE_SUBJECT)
        decision_engine = (response or {}).get("decision_engine") or {}
        decision_id = decision_engine.get("decision_id")
        signal = decision_engine.get("final_signal") or decision_engine.get("signal") or "NO_TRADE"
        confidence = decision_engine.get("directional_confidence")
        log_event(logger, message="decision_loaded", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  decision_id=decision_id, signal=signal)

        # Re-read the persisted decision rather than trusting the in-memory
        # response dict's eligible_for_execution: execution_approved is set
        # by execution_gate.finalize_decision_for_execution() AFTER persist,
        # and additionally factors in the portfolio risk gate - a decision
        # can be model-eligible but still correctly risk-blocked.
        from app.db.models import ActiveDriveDecision
        check_db = SessionLocal()
        try:
            persisted_row = check_db.get(ActiveDriveDecision, decision_id) if decision_id else None
            execution_approved = bool(persisted_row and persisted_row.execution_approved)
            final_block_reason = persisted_row.final_block_reason if persisted_row else None
        finally:
            check_db.close()

        if not decision_id or signal not in ("LONG", "SHORT") or not execution_approved:
            log_event(logger, message="scheduler_no_trade", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      decision_id=decision_id, reason=final_block_reason or "No execution-approved V2 decision this cycle")
            log_event(logger, message="scheduler_cycle_completed", category="scheduler", symbol=symbol,
                      cycle_id=cycle_id, decision_id=decision_id, outcome="no_trade")
            return

        log_event(
            logger, message="scheduler_cycle", category="scheduler", symbol=symbol, cycle_id=cycle_id,
            decision_id=decision_id, direction=signal, confidence=confidence,
            reason="Execution-approved Active Drive V2 decision persisted",
        )
        log_event(logger, message="authority_granted", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  decision_id=decision_id, direction=signal)

        log_event(logger, message="execution_requested", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  decision_id=decision_id)
        result = await execution_router.open_position(
            symbol=symbol,
            clamp_to_max=True,
            order_type=OrderType.IOC,
            automated_execution=True,
            execution_lease_owner=execution_lease_owner,
            decision_id=decision_id,
            user_id=INTERNAL_SERVICE_SUBJECT,
            cycle_id=cycle_id,
        )

        log_event(
            logger, message="scheduler_execution_result", category="scheduler", symbol=symbol, cycle_id=cycle_id,
            decision_id=decision_id, mode=result.mode, reason=result.reason if not result.ok else None,
        )
        if result.ok:
            _record_candidate(symbol, "TRADE_APPROVED", "Order accepted", direction=signal,
                              confidence=confidence, mode=result.mode)
            stage_event = "paper_order_accepted" if result.mode == modes.MODE_PAPER else "order_accepted"
            log_event(logger, message=stage_event, category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      decision_id=decision_id, mode=result.mode)
            log_event(logger, message="position_opened", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      decision_id=decision_id, mode=result.mode)
        else:
            reason = result.reason or "Execution failed"
            lower = reason.lower()
            outcome = ("BLOCKED_BY_BALANCE" if any(x in lower for x in ("balance", "margin", "notional"))
                       else "BLOCKED_BY_EXCHANGE" if result.mode != modes.MODE_PAPER
                       else "EXECUTION_FAILED")
            _record_candidate(symbol, outcome, reason, direction=signal, confidence=confidence, mode=result.mode)
            log_event(logger, message="execution_failed", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      decision_id=decision_id, mode=result.mode, reason=reason)
        log_event(logger, message="scheduler_cycle_completed", category="scheduler", symbol=symbol,
                  cycle_id=cycle_id, decision_id=decision_id, outcome=result.mode if result.ok else "blocked")
