"""Paper-trading margin, notional and liquidation math.

Every function here is pure (no DB, no network) so the same code can be
unit-tested directly and shared by the API layer (app/api/paper.py) and the
position manager loop.

The liquidation price is an ESTIMATE for the paper engine only: isolated
margin, linear USDT-margined position, one flat maintenance-margin rate, no
fee/funding modelling and no tiered maintenance brackets. It deliberately
does not claim to reproduce any specific exchange's formula - the UI must
always label it "Estimated liquidation".
"""

from __future__ import annotations

import math

# Flat maintenance-margin rate applied to every paper position. 0.5% is the
# lowest Binance USDT-M tier, used here as a plain constant - the paper
# engine has no notional-tier table.
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005

MIN_LEVERAGE = 1.0
MAX_LEVERAGE = 125.0

# SL/TP edits must sit at least this far (percent of the reference price)
# away from it, so a stop can't be parked on top of the mark and fire on the
# next tick's noise.
MIN_RISK_DISTANCE_PCT = 0.05


class RiskValidationError(Exception):
    """A rejected SL/TP/trailing-stop value, carrying which field failed so
    the API can return {"detail": ..., "field": ...}."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.message = message
        self.field = field


def _is_price(v: float | None) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def estimated_liquidation_price(
    side: str,
    entry_price: float | None,
    leverage: float | None,
    maintenance_margin_rate: float | None = None,
) -> float | None:
    """Estimated liquidation price for an isolated, linear paper position.

    Derivation - liquidation happens when equity in the position falls to
    the maintenance margin (both sides valued at the mark price P):

        initial_margin + unrealized_pnl = maintenance_margin
        LONG:  (E*q)/L + (P - E)*q = P*q*mmr  ->  P = E * (1 - 1/L) / (1 - mmr)
        SHORT: (E*q)/L + (E - P)*q = P*q*mmr  ->  P = E * (1 + 1/L) / (1 + mmr)

    Returns None when inputs are missing/invalid or the result is not
    meaningful (a 1x LONG's estimate is <= 0: the position can lose at most
    its own notional, so there is no liquidation level above zero).
    """
    if not _is_price(entry_price) or leverage is None:
        return None
    if not isinstance(leverage, (int, float)) or not math.isfinite(leverage) or leverage < MIN_LEVERAGE:
        return None
    mmr = DEFAULT_MAINTENANCE_MARGIN_RATE if maintenance_margin_rate is None else maintenance_margin_rate
    if not (0 <= mmr < 1):
        return None

    if side == "LONG":
        liq = entry_price * (1 - 1 / leverage) / (1 - mmr)
        return liq if liq > 0 else None
    if side == "SHORT":
        return entry_price * (1 + 1 / leverage) / (1 + mmr)
    return None


def distance_to_liquidation_pct(side: str, mark_price: float | None, liquidation_price: float | None) -> float | None:
    """How far (percent of mark) the mark price is from the estimated
    liquidation level. 0 means at/past liquidation."""
    if not _is_price(mark_price) or not _is_price(liquidation_price):
        return None
    if side == "LONG":
        dist = (mark_price - liquidation_price) / mark_price * 100
    elif side == "SHORT":
        dist = (liquidation_price - mark_price) / mark_price * 100
    else:
        return None
    return max(0.0, dist)


def risk_reward_ratio(side: str, entry_price: float | None, stop_loss: float | None, take_profit: float | None) -> float | None:
    """Reward-per-unit-risk implied by the current SL/TP, from entry."""
    if not _is_price(entry_price) or not _is_price(stop_loss) or not _is_price(take_profit):
        return None
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    if risk <= 0:
        return None
    return reward / risk


def unrealized_pnl(side: str, entry_price: float, mark_price: float, qty: float) -> float:
    if side == "LONG":
        return (mark_price - entry_price) * qty
    return (entry_price - mark_price) * qty


def validate_risk_levels(
    side: str,
    reference_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    trailing_stop: float | None = None,
    min_distance_pct: float = MIN_RISK_DISTANCE_PCT,
) -> None:
    """Rejects invalid SL/TP/trailing values for an open position, raising
    RiskValidationError with the offending field. reference_price is the
    price the levels are checked against (current mark for live edits,
    entry price when opening)."""
    if side not in ("LONG", "SHORT"):
        raise RiskValidationError(f"Unknown position side {side!r}", field="side")
    if not _is_price(reference_price):
        raise RiskValidationError("No valid reference price available to validate against", field="reference_price")

    min_distance = reference_price * min_distance_pct / 100

    for field, value in (("stop_loss", stop_loss), ("take_profit", take_profit)):
        if value is None:
            continue
        if not _is_price(value):
            raise RiskValidationError(f"{field} must be a positive number", field=field)
        if abs(value - reference_price) < min_distance:
            raise RiskValidationError(
                f"{field} is too close to the current price ({reference_price:.2f}); "
                f"minimum distance is {min_distance_pct}%",
                field=field,
            )

    if stop_loss is not None and take_profit is not None and stop_loss == take_profit:
        raise RiskValidationError("take_profit must differ from stop_loss", field="take_profit")

    if side == "LONG":
        if stop_loss is not None and stop_loss >= reference_price:
            raise RiskValidationError("Invalid stop loss for LONG position: must be below the current price", field="stop_loss")
        if take_profit is not None and take_profit <= reference_price:
            raise RiskValidationError("Invalid take profit for LONG position: must be above the current price", field="take_profit")
    else:
        if stop_loss is not None and stop_loss <= reference_price:
            raise RiskValidationError("Invalid stop loss for SHORT position: must be above the current price", field="stop_loss")
        if take_profit is not None and take_profit >= reference_price:
            raise RiskValidationError("Invalid take profit for SHORT position: must be below the current price", field="take_profit")

    if trailing_stop is not None:
        if not _is_price(trailing_stop):
            raise RiskValidationError("trailing_stop must be a positive price distance", field="trailing_stop")
        if trailing_stop < min_distance:
            raise RiskValidationError(
                f"trailing_stop distance is below the minimum ({min_distance_pct}% of price)",
                field="trailing_stop",
            )
        if trailing_stop >= reference_price:
            raise RiskValidationError("trailing_stop distance must be smaller than the price itself", field="trailing_stop")
