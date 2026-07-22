"""Risk-direction classification for threshold edits (Bot Settings Part 3).

Lowering the confidence/point-margin/evidence gates makes MORE signals pass,
which is a riskier configuration - especially on scope="binance_real". This
module only classifies direction; it never blocks a save itself (the API
layer in app/api/risk.py is what returns 409 CONFIRMATION_REQUIRED). Kept
separate from settings_repository.py so that module's "never imports
app.trading.modes" guarantee stays trivially auditable - this file has no
imports at all beyond the constant it re-exports.
"""
from app.risk.settings_repository import LOWER_IS_RISKIER


def is_lowering(field: str, old_value, new_value) -> bool:
    """True if `field` is one of the confidence/point-margin/evidence gates
    and new_value represents a laxer (lower) requirement than old_value."""
    if field not in LOWER_IS_RISKIER:
        return False
    try:
        return float(new_value) < float(old_value)
    except (TypeError, ValueError):
        return False


def lowered_fields(patch: dict, current: dict) -> list:
    """Returns the subset of LOWER_IS_RISKIER fields in `patch` that would be
    lowered relative to `current` (the scope's persisted values)."""
    return [
        field for field in LOWER_IS_RISKIER
        if field in patch and is_lowering(field, current.get(field), patch[field])
    ]
