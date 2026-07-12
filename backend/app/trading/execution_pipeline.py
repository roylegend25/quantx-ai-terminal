"""Execution-pipeline instrumentation (Phase 25).

The decision engine (app.api.prediction) says a signal is "allowed" purely
on model/confidence/risk-settings grounds - it has no idea whether an order
actually reached Binance, because BinanceExecutionProvider.open_position()
(app.trading.execution_router) runs afterward, in a completely different
call, and can fail for reasons the decision engine never sees: quantity
rounds to zero, margin/leverage validation is rejected, the exchange itself
refuses the order. Historically that gap was invisible - a "trade allowed"
signal with a silently-failed execution looked identical to one that just
never fired.

This module gives every open_position() attempt a stage-by-stage record
(PipelineRecorder, persisted as one BinanceExecutionAttempt row) and a
fixed, operator-facing reason vocabulary, so the Execution Pipeline card
can show exactly which stage failed and why - never just "allowed" or
silence.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.db.models import BinanceExecutionAttempt
from app.db.session import SessionLocal

STAGE_SIGNAL_GENERATED = "signal_generated"
STAGE_CHAMPION_MODEL = "champion_model"
STAGE_RISK_GATE = "risk_gate"
STAGE_POSITION_SIZING = "position_sizing"
STAGE_QUANTITY_CALCULATION = "quantity_calculation"
STAGE_BINANCE_VALIDATION = "binance_validation"
STAGE_ENTRY_ORDER_SUBMITTED = "entry_order_submitted"
STAGE_ENTRY_ORDER_ACCEPTED = "entry_order_accepted"
# Phase 27: a filled entry is not yet a complete, safe trade - protection
# (TP/SL) must be confirmed on the exchange before the pipeline calls the
# attempt done. See app.trading.protection and BinanceExecutionProvider.
# Phase 26 Algo Order Provider: which protection surface (classic vs algo,
# see app.trading.protection_provider) was used is now itself a visible
# pipeline stage, and a fully verified trade gets one final "trade active"
# stage instead of ending silently at protection_confirmed.
STAGE_PROTECTION_PROVIDER_SELECTED = "protection_provider_selected"
STAGE_PROTECTIVE_TP_SUBMITTED = "protective_tp_submitted"
STAGE_PROTECTIVE_SL_SUBMITTED = "protective_sl_submitted"
STAGE_PROTECTION_CONFIRMED = "protection_confirmed"
STAGE_TRADE_ACTIVE = "trade_active"

# Display order for the frontend pipeline diagram.
STAGE_ORDER = [
    STAGE_SIGNAL_GENERATED,
    STAGE_CHAMPION_MODEL,
    STAGE_RISK_GATE,
    STAGE_POSITION_SIZING,
    STAGE_QUANTITY_CALCULATION,
    STAGE_BINANCE_VALIDATION,
    STAGE_ENTRY_ORDER_SUBMITTED,
    STAGE_ENTRY_ORDER_ACCEPTED,
    STAGE_PROTECTION_PROVIDER_SELECTED,
    STAGE_PROTECTIVE_TP_SUBMITTED,
    STAGE_PROTECTIVE_SL_SUBMITTED,
    STAGE_PROTECTION_CONFIRMED,
    STAGE_TRADE_ACTIVE,
]

FINAL_STATUS_ORDER_ACCEPTED = "order_accepted"
FINAL_STATUS_PROTECTION_FAILED = "PROTECTION_FAILED"
FINAL_STATUS_FAILED = "failed"

STATUS_SUCCESS = "success"
STATUS_WAITING = "waiting"
STATUS_FAILED = "failed"

# app.trading.real_risk_gate check name -> the fixed operator-facing phrase.
# Anything not listed falls back to "Risk gate rejected" (the exact gate
# reason string is still carried separately in final_reason).
_GATE_REASON_LABELS = {
    "symbol_allowed": "Symbol disabled",
    "notional": "Quantity below minimum",
    "max_open_positions": "Existing position",
    "confidence": "Confidence dropped",
    "spread": "Spread too high",
    "duplicate": "Duplicate order protection",
    "exchange_reachable": "API timeout",
    "balance": "Insufficient margin",
    "tp_sl_required": "TP/SL required",
    "existing_position_protected": "Existing live position is unprotected",
}


def classify_gate_failure(check_name: str | None) -> str:
    return _GATE_REASON_LABELS.get(check_name or "", "Risk gate rejected")


def classify_binance_error(exc: Exception) -> str:
    """Map a raised exception during order placement to the fixed
    operator-facing vocabulary requested for the Execution Pipeline card.
    The underlying message is preserved separately (final_reason /
    exchange_response) - this is only the one-line stage label."""
    from app.exchanges.binance_errors import (
        BinanceDuplicateOrder,
        BinanceError,
        BinanceInsufficientBalance,
        BinanceInvalidSymbol,
        BinanceNetworkError,
        BinanceNotConfigured,
        BinancePermissionError,
        BinanceRateLimitError,
        BinanceTimestampError,
    )
    from app.trading.margin import RiskValidationError

    if isinstance(exc, BinanceInsufficientBalance):
        return "Insufficient margin"
    if isinstance(exc, BinanceDuplicateOrder):
        return "Duplicate order protection"
    if isinstance(exc, BinanceInvalidSymbol):
        return "Symbol disabled"
    if isinstance(exc, (BinanceTimestampError, BinanceNetworkError, BinanceRateLimitError)):
        return "API timeout"
    if isinstance(exc, (BinancePermissionError, BinanceNotConfigured)):
        return "Signature invalid"
    if isinstance(exc, RiskValidationError):
        return "Binance rejected order"
    if isinstance(exc, BinanceError):
        code = getattr(exc, "code", None)
        msg = (getattr(exc, "message", "") or "").upper()
        if code == -1022:
            return "Signature invalid"
        if code == -2022:
            return "Reduce-only conflict"
        if code == -4061:
            return "Position mode mismatch"
        if code == -4015:
            return "Duplicate order protection"
        if code in (-1013, -4003, -4014) or "LOT_SIZE" in msg or "PRECISION" in msg or "TICK_SIZE" in msg:
            return "Precision mismatch"
        return "Binance rejected order"
    return "Binance rejected order"


class PipelineRecorder:
    """Accumulates the stage-by-stage record for one open_position() call
    and persists it exactly once, via finish(). Never raises - a recorder
    failure must never unwind a live order attempt."""

    def __init__(self, *, mode: str, symbol: str, side: str | None, is_test: bool = False,
                confidence: float | None = None, requested_notional: float | None = None,
                leverage: float | None = None):
        self._start = time.perf_counter()
        self.mode = mode
        self.symbol = symbol
        self.side = side
        self.is_test = is_test
        self.confidence = confidence
        self.requested_notional = requested_notional
        self.requested_quantity: float | None = None
        self.adjusted_quantity: float | None = None
        self.leverage = leverage
        self.margin_required: float | None = None
        self.order_id: int | None = None
        self.stages: list[dict] = []
        self.stage(STAGE_SIGNAL_GENERATED, STATUS_SUCCESS)

    def stage(self, name: str, status: str, reason: str | None = None) -> None:
        self.stages.append({
            "stage": name,
            "status": status,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def finish(self, final_status: str, reason: str | None = None, exchange_response: dict | None = None) -> None:
        latency_ms = round((time.perf_counter() - self._start) * 1000, 2)
        db = SessionLocal()
        try:
            db.add(BinanceExecutionAttempt(
                mode=self.mode,
                symbol=self.symbol,
                side=self.side,
                is_test=self.is_test,
                confidence=self.confidence,
                requested_notional=self.requested_notional,
                requested_quantity=self.requested_quantity,
                adjusted_quantity=self.adjusted_quantity,
                leverage=self.leverage,
                margin_required=self.margin_required,
                stages=self.stages,
                final_status=final_status,
                final_reason=reason,
                exchange_response=exchange_response,
                order_id=self.order_id,
                latency_ms=latency_ms,
            ))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
