"""Live Margin Calculator (Phase 26) - pure, unit-testable math over real
Binance account state, exchange filters, commission rates and leverage
brackets. Every number here is either read directly from Binance (by the
caller, in app/api/trading_control.py) or derived from those real numbers
by a documented formula below - never a guessed constant standing in for
exchange data.

This module never places or blocks an order - app/trading/real_risk_gate.py
remains the sole authority for whether a real order is actually allowed.
Everything here is advisory: "what would happen if", computed so a
rejection is never a surprise and a safe position size is never a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------- rounding


def round_qty(qty: float, step_size: float, precision: int) -> float:
    """Floor `qty` to the exchange's step size, then to its display
    precision - mirrors execution_router._round_qty's flooring behavior
    (Binance rejects a quantity with more precision than stepSize)."""
    if qty <= 0:
        return 0.0
    stepped = math.floor(qty / step_size) * step_size if step_size > 0 else qty
    return round(stepped, precision)


def round_price(price: float, tick_size: float, precision: int) -> float:
    if price <= 0:
        return 0.0
    stepped = round(price / tick_size) * tick_size if tick_size > 0 else price
    return round(stepped, precision)


# --------------------------------------------------------- maintenance margin


def maintenance_margin_for_notional(brackets: list[dict], notional: float) -> tuple[float, float, bool]:
    """(maint_margin_ratio, cum, brackets_available) for the bracket this
    notional falls into. Falls back to a documented conservative Tier-1-like
    estimate (0.4%, 0 cum) only when the real brackets couldn't be read -
    brackets_available=False tells the caller to label the number as
    estimated rather than exact."""
    if not brackets:
        return 0.004, 0.0, False
    for b in brackets:
        cap = b["notional_cap"]
        if cap <= 0 or notional <= cap:
            return b["maint_margin_ratio"], b["cum"], True
    last = brackets[-1]
    return last["maint_margin_ratio"], last["cum"], True


# ------------------------------------------------------------- margin math


@dataclass
class MarginBreakdown:
    notional: float
    leverage: float
    initial_margin: float
    estimated_fees: float
    maintenance_margin: float
    safety_buffer: float
    total_required: float
    available_margin: float
    status: str  # PASS | FAIL
    missing: float

    def to_dict(self) -> dict:
        return {
            "notional": round(self.notional, 2),
            "leverage": self.leverage,
            "initial_margin": round(self.initial_margin, 2),
            "estimated_fees": round(self.estimated_fees, 4),
            "maintenance_margin": round(self.maintenance_margin, 4),
            "safety_buffer": round(self.safety_buffer, 2),
            "total_required": round(self.total_required, 2),
            "available_margin": round(self.available_margin, 2),
            "status": self.status,
            "missing": round(self.missing, 2),
        }


def compute_margin_breakdown(
    *, notional: float, leverage: float, available_margin: float,
    taker_fee_rate: float, maint_margin_ratio: float, maint_cum: float,
    safety_buffer_usdt: float, safety_buffer_pct: float,
) -> MarginBreakdown:
    """The exact, never-hidden arithmetic behind a PASS/FAIL verdict:

        initial_margin      = notional / leverage
        estimated_fees      = notional * taker_fee_rate * 2   (round-trip: entry + exit, both taker)
        maintenance_margin  = max(0, notional * maint_margin_ratio - maint_cum)
        safety_buffer       = max(safety_buffer_usdt, safety_buffer_pct * available_margin)
        total_required      = initial_margin + estimated_fees + maintenance_margin + safety_buffer
        status              = PASS if available_margin >= total_required else FAIL
    """
    leverage = max(leverage, 1.0)
    initial_margin = notional / leverage
    fees = notional * taker_fee_rate * 2
    maintenance_margin = max(0.0, notional * maint_margin_ratio - maint_cum)
    safety_buffer = max(safety_buffer_usdt, safety_buffer_pct * available_margin)
    total_required = initial_margin + fees + maintenance_margin + safety_buffer
    missing = max(0.0, total_required - available_margin)
    status = "PASS" if missing <= 1e-9 else "FAIL"
    return MarginBreakdown(
        notional=notional, leverage=leverage, initial_margin=initial_margin, estimated_fees=fees,
        maintenance_margin=maintenance_margin, safety_buffer=safety_buffer, total_required=total_required,
        available_margin=available_margin, status=status, missing=missing,
    )


def recommended_max_notional(
    *, available_margin: float, leverage: float, taker_fee_rate: float, maint_margin_ratio: float,
    safety_buffer_usdt: float, safety_buffer_pct: float,
) -> tuple[float, float]:
    """Solves compute_margin_breakdown's total_required == available_margin
    for notional, with the safety buffer reserved up front (a fixed slice
    of available_margin, not scaling with position size - so the buffer
    never erodes as the recommendation grows). Returns
    (recommended_max_notional, reserved_buffer)."""
    leverage = max(leverage, 1.0)
    reserved = max(safety_buffer_usdt, safety_buffer_pct * available_margin)
    spendable = max(0.0, available_margin - reserved)
    denom = (1.0 / leverage) + (taker_fee_rate * 2) + maint_margin_ratio
    max_notional = spendable / denom if denom > 0 else 0.0
    return max_notional, reserved


def max_tradable_quantity(
    *, available_margin: float, price: float, leverage: float, step_size: float, precision: int, min_qty: float,
) -> tuple[float, float]:
    """The hard exchange ceiling: what the wallet could technically support
    at full leverage with ZERO fee/maintenance/safety headroom - NOT a
    recommendation (see recommended_max_notional for the safe number).
    Returns (quantity, notional)."""
    if price <= 0:
        return 0.0, 0.0
    raw_qty = (available_margin * max(leverage, 1.0)) / price
    qty = round_qty(raw_qty, step_size, precision)
    if qty < min_qty:
        return 0.0, 0.0
    return qty, round(qty * price, 2)


# ------------------------------------------------------------- liquidation


def estimate_liquidation_price(*, entry_price: float, leverage: float, maint_margin_ratio: float, side: str) -> float:
    """Standard simplified isolated-margin liquidation estimate (ignores
    funding and other positions' cross-margin effects) - the same
    approximation most futures calculators use. The REAL liquidation price
    for an actually-open position always comes straight from Binance
    (BinancePosition.liquidation_price); this is a PRE-TRADE planning
    estimate only, and callers must label it as such."""
    leverage = max(leverage, 1.0)
    if side.upper() == "LONG":
        return max(0.0, entry_price * (1 - 1 / leverage + maint_margin_ratio))
    return entry_price * (1 + 1 / leverage - maint_margin_ratio)


# --------------------------------------------------------------- risk gauge


def risk_capacity_score(components: dict[str, float | None]) -> tuple[float, str]:
    """0-100 risk CAPACITY (higher = safer / more room left), the inverse
    framing of the Risk Management page's risk_score. Each value in
    `components` must already be normalized to 0-100 "capacity" (100 = no
    concern, 0 = maxed out) - unavailable inputs (None) are excluded from
    the average entirely rather than assumed safe. Returns (score, level)
    where level is GREEN/YELLOW/ORANGE/RED."""
    available = [v for v in components.values() if v is not None]
    score = round(sum(available) / len(available), 1) if available else 50.0
    score = min(100.0, max(0.0, score))
    if score >= 75:
        level = "GREEN"
    elif score >= 50:
        level = "YELLOW"
    elif score >= 25:
        level = "ORANGE"
    else:
        level = "RED"
    return score, level
