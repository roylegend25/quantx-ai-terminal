"""Authoritative prediction lifecycle status and outcome calculation.

One documented set of rules, shared by the resolver, the dashboard, and the
decision-engine calibration dataset - never re-implemented ad hoc per
consumer. This module never places an order and never fabricates a price;
it only classifies and scores predictions that already have real, stored
reference/outcome prices.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings

# Explicit lifecycle states. Replaces the old single generic "UNRESOLVED"
# label - every prediction is in exactly one of these at any moment, and
# every matured prediction eventually reaches a terminal one (RESOLVED_* or
# VOID_*). VOID states are terminal and permanently excluded from accuracy/
# calibration calculations; PENDING/RESOLVING/RETRYING are never treated as
# failures.
PENDING = "PENDING"
RESOLVING = "RESOLVING"
RESOLVED_CORRECT = "RESOLVED_CORRECT"
RESOLVED_WRONG = "RESOLVED_WRONG"
RESOLVED_NEUTRAL = "RESOLVED_NEUTRAL"
VOID_DATA_GAP = "VOID_DATA_GAP"
VOID_INVALID_PREDICTION = "VOID_INVALID_PREDICTION"
RESOLUTION_ERROR_RETRYING = "RESOLUTION_ERROR_RETRYING"

TERMINAL_STATUSES = frozenset({RESOLVED_CORRECT, RESOLVED_WRONG, RESOLVED_NEUTRAL, VOID_DATA_GAP, VOID_INVALID_PREDICTION})
VOID_STATUSES = frozenset({VOID_DATA_GAP, VOID_INVALID_PREDICTION})
RESOLVED_STATUSES = frozenset({RESOLVED_CORRECT, RESOLVED_WRONG, RESOLVED_NEUTRAL})
TRUSTWORTHY_STATUSES = RESOLVED_STATUSES  # only these may ever feed calibration/strategy evaluation

# unresolved_status reasons (app.decision_engine.resolver.classify_unresolved_reason)
# that mean "this row can never resolve as stored, no amount of retrying
# will fix it" - terminal VOID, not a retry state.
_INVALID_PREDICTION_REASONS = frozenset({"invalid_due_time", "missing_entry_price", "legacy_missing_metadata", "unsupported_timeframe", "unsupported_symbol"})
_DATA_GAP_REASONS = frozenset({"permanent_data_gap"})


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def lifecycle_status_for_attempt(unresolved_reason: str | None) -> str:
    """What lifecycle_status a failed resolution attempt should record,
    given classify_unresolved_reason's output. Only called on failure -
    success always writes RESOLVED_*, computed separately by
    resolve_prediction_outcome below."""
    if unresolved_reason in _DATA_GAP_REASONS:
        return VOID_DATA_GAP
    if unresolved_reason in _INVALID_PREDICTION_REASONS:
        return VOID_INVALID_PREDICTION
    return RESOLUTION_ERROR_RETRYING


def effective_lifecycle_status(stored_status: str | None, resolution_deadline: datetime | None, now: datetime | None = None) -> str:
    """The status to actually display/count right now. A stored terminal or
    retrying status is authoritative and returned as-is. A row that has
    never been touched (stored_status is PENDING or None) is derived live
    from the deadline: PENDING before it matures, RESOLVING the instant it
    matures - without needing a write the exact moment every prediction
    crosses its deadline, which would mean re-touching the entire backlog
    every second for no functional benefit."""
    if stored_status and stored_status not in (None, PENDING):
        return stored_status
    now = now or datetime.now(timezone.utc)
    deadline = _aware(resolution_deadline)
    if deadline is not None and deadline > now:
        return PENDING
    return RESOLVING


class ResolvedOutcome:
    """Documented outcome of one resolved prediction. Every resolved
    prediction stores its own formula/threshold snapshot (neutral_band,
    fee_rate, slippage_bps) so a later config change never rewrites
    history - the stored values on PredictionResolution are what actually
    scored this row, not whatever settings currently say."""

    __slots__ = ("reference_price", "outcome_price", "raw_return", "direction_adjusted_return",
                 "estimated_fee", "estimated_slippage", "net_direction_adjusted_return",
                 "classification", "neutral_band_used", "fee_rate_used", "slippage_bps_used")

    def __init__(self, reference_price, outcome_price, raw_return, direction_adjusted_return,
                 estimated_fee, estimated_slippage, net_direction_adjusted_return,
                 classification, neutral_band_used, fee_rate_used, slippage_bps_used):
        self.reference_price = reference_price
        self.outcome_price = outcome_price
        self.raw_return = raw_return
        self.direction_adjusted_return = direction_adjusted_return
        self.estimated_fee = estimated_fee
        self.estimated_slippage = estimated_slippage
        self.net_direction_adjusted_return = net_direction_adjusted_return
        self.classification = classification  # RESOLVED_CORRECT | RESOLVED_WRONG | RESOLVED_NEUTRAL
        self.neutral_band_used = neutral_band_used
        self.fee_rate_used = fee_rate_used
        self.slippage_bps_used = slippage_bps_used


def resolve_prediction_outcome(direction: str, reference_price: float, outcome_price: float,
                                *, neutral_band: float | None = None, fee_rate: float | None = None,
                                slippage_bps: float | None = None) -> ResolvedOutcome:
    """The one documented outcome function - shared by the resolver, backtest
    replay, paper-trade reconciliation, and dashboard/model evaluation.
    Never fabricates a price: both reference_price and outcome_price must
    already be real, stored values before this is called.

    A LONG is correct only when the net (cost-adjusted) directional return
    clears the neutral band on the positive side; a SHORT only on the
    negative side. Moves whose magnitude never clears the band are
    RESOLVED_NEUTRAL - never counted as a directional win or loss. NO_TRADE
    predictions get a RESOLVED_NEUTRAL classification too (there was no
    directional call to be right or wrong about); callers that need a
    genuinely non-directional record should treat classification as
    informational only for those and exclude them from hit-rate math
    exactly as they already exclude RESOLVED_NEUTRAL.
    """
    band = abs(neutral_band if neutral_band is not None else settings.resolution_neutral_band)
    fee = fee_rate if fee_rate is not None else settings.active_drive_estimated_taker_fee_rate
    slippage = slippage_bps if slippage_bps is not None else settings.active_drive_estimated_slippage_bps
    raw_return = (outcome_price - reference_price) / reference_price
    signed = 1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0
    direction_adjusted_return = raw_return * signed if signed else raw_return
    cost = fee * 2 + (slippage / 10_000.0) * 2  # round-trip: entry + exit
    net_return = direction_adjusted_return - cost if signed else direction_adjusted_return
    if not signed or abs(net_return) <= band:
        classification = RESOLVED_NEUTRAL
    elif net_return > 0:
        classification = RESOLVED_CORRECT
    else:
        classification = RESOLVED_WRONG
    return ResolvedOutcome(
        reference_price=reference_price, outcome_price=outcome_price, raw_return=raw_return,
        direction_adjusted_return=direction_adjusted_return,
        estimated_fee=fee * 2, estimated_slippage=(slippage / 10_000.0) * 2,
        net_direction_adjusted_return=net_return, classification=classification,
        neutral_band_used=band, fee_rate_used=fee, slippage_bps_used=slippage,
    )
