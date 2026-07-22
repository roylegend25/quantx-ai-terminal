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
        from app.api.timeframes import evaluate_and_issue_horizon_authority
        horizon = await evaluate_and_issue_horizon_authority(
            user_id=INTERNAL_SERVICE_SUBJECT, account_id="default", symbol=symbol,
            evaluation_reason="scheduler", idempotency_key=cycle_id)
        log_event(logger, message="decision_loaded", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  horizon_decision_id=horizon.get("horizon_decision_id"),
                  authority_status=horizon.get("authority_status"))
        if horizon.get("authority_status") != "persisted_authority" or not horizon.get("horizon_decision_id"):
            log_event(logger, message="scheduler_no_trade", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      reason="Trading Horizon authority was not issued")
            log_event(logger, message="scheduler_cycle_completed", category="scheduler", symbol=symbol,
                      cycle_id=cycle_id, outcome="no_authority")
            return
        log_event(
            logger,
            message="scheduler_cycle",
            category="scheduler",
            symbol=symbol,
            cycle_id=cycle_id,
            horizon_decision_id=horizon["horizon_decision_id"],
            direction=horizon["direction"],
            confidence=horizon.get("calibrated_confidence"),
            reason="Persisted Trading Horizon authority issued",
        )
        log_event(logger, message="authority_granted", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  horizon_decision_id=horizon["horizon_decision_id"], direction=horizon["direction"])

        # The router loads direction, timeframe, confidence, stop, target and
        # evidence from the immutable snapshot. This call carries no model output.
        log_event(logger, message="execution_requested", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                  horizon_decision_id=horizon["horizon_decision_id"])
        result = await execution_router.open_position(
            symbol=symbol,
            clamp_to_max=True,
            order_type=OrderType.IOC,
            automated_execution=True,
            execution_lease_owner=execution_lease_owner,
            horizon_decision_id=horizon["horizon_decision_id"],
            user_id=horizon["user_id"],
            cycle_id=cycle_id,
        )

        log_event(
            logger,
            message="scheduler_execution_result",
            category="scheduler",
            symbol=symbol,
            cycle_id=cycle_id,
            horizon_decision_id=horizon["horizon_decision_id"],
            mode=result.mode,
            reason=result.reason if not result.ok else None,
        )
        if result.ok:
            _record_candidate(symbol, "TRADE_APPROVED", "Order accepted",
                              direction=horizon["direction"],
                              confidence=horizon.get("calibrated_confidence"), mode=result.mode)
            stage_event = "paper_order_accepted" if result.mode == modes.MODE_PAPER else "order_accepted"
            log_event(logger, message=stage_event, category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      horizon_decision_id=horizon["horizon_decision_id"], mode=result.mode)
            log_event(logger, message="position_opened", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      horizon_decision_id=horizon["horizon_decision_id"], mode=result.mode)
        else:
            reason = result.reason or "Execution failed"
            lower = reason.lower()
            outcome = ("BLOCKED_BY_BALANCE" if any(x in lower for x in ("balance", "margin", "notional"))
                       else "BLOCKED_BY_EXCHANGE" if result.mode != modes.MODE_PAPER
                       else "EXECUTION_FAILED")
            _record_candidate(symbol, outcome, reason, direction=horizon["direction"],
                              confidence=horizon.get("calibrated_confidence"), mode=result.mode)
            log_event(logger, message="execution_failed", category="scheduler", symbol=symbol, cycle_id=cycle_id,
                      horizon_decision_id=horizon["horizon_decision_id"], mode=result.mode, reason=reason)
        log_event(logger, message="scheduler_cycle_completed", category="scheduler", symbol=symbol,
                  cycle_id=cycle_id, horizon_decision_id=horizon["horizon_decision_id"], outcome=result.mode if result.ok else "blocked")
