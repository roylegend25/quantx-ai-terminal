"""Editable paper-trading risk limits, persisted in SQLite.

Singleton row (id=1 of RiskSettings). Read fresh from the DB on every call
rather than cached in memory, so a PUT from the dashboard takes effect on
the very next prediction/scheduler cycle without a restart - the whole
point of moving these out of the static, env-only app/core/config.py
Settings object.
"""
from datetime import datetime, timezone

from app.db.models import RiskSettings
from app.db.session import SessionLocal

# Doubles as the "Balanced" preset and the reset-to-defaults target.
DEFAULTS = {
    "min_confidence_to_trade": 0.60,
    "max_risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 2.0,
    "max_weekly_loss_pct": 6.0,
    "max_drawdown_pct": 10.0,
    "max_consecutive_losses": 5,
    "max_open_positions": 1,
    "max_position_size_usd": 1000.0,
    "allow_long": True,
    "allow_short": True,
    "cooldown_minutes": 0,
    "paper_trading_enabled": True,
    # Live Margin Calculator's advisory reserve (app/trading/margin_calculator.py) -
    # never consulted by real_risk_gate.py itself.
    "safety_buffer_usdt": 1.0,
    "safety_buffer_pct": 0.10,
}

# Inclusive (min, max) bounds - the one place "unsafe" numeric values get
# vetoed, so every caller (API, presets) goes through the same gate.
BOUNDS = {
    "min_confidence_to_trade": (0.05, 0.95),
    "max_risk_per_trade_pct": (0.1, 5.0),
    "max_daily_loss_pct": (0.5, 10.0),
    "max_weekly_loss_pct": (1.0, 30.0),
    "max_drawdown_pct": (1.0, 50.0),
    "max_consecutive_losses": (1, 20),
    "max_open_positions": (1, 5),
    "max_position_size_usd": (10.0, 100_000.0),
    "cooldown_minutes": (0, 1440),
    "safety_buffer_usdt": (0.0, 1000.0),
    "safety_buffer_pct": (0.0, 0.90),
}

BOOL_FIELDS = ("allow_long", "allow_short", "paper_trading_enabled")
INT_FIELDS = ("max_consecutive_losses", "max_open_positions", "cooldown_minutes")

FIELDS = tuple(DEFAULTS.keys())


class InvalidRiskSetting(ValueError):
    pass


def _row_to_dict(row: RiskSettings) -> dict:
    return {
        **{field: getattr(row, field) for field in FIELDS},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _get_or_create(db) -> RiskSettings:
    row = db.get(RiskSettings, 1)
    if not row:
        row = RiskSettings(id=1, **DEFAULTS)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_settings(db=None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        return _row_to_dict(_get_or_create(db))
    finally:
        if owns_session:
            db.close()


def get_user_settings(user_id: str, db=None) -> dict:
    """Ownership-aware read boundary for automated sizing.

    Current installations have one configured trading account and therefore
    one risk row; the returned owner stamp prevents that row from being reused
    under a different authority identity inside the sizing pipeline.
    """
    return {**get_settings(db), "risk_policy_owner": str(user_id)}


def validate(patch: dict) -> None:
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise InvalidRiskSetting(f"Unknown risk setting: {key}")

        if key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise InvalidRiskSetting(f"{key} must be true or false")
            continue

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRiskSetting(f"{key} must be a number")

        if key in INT_FIELDS and value != int(value):
            raise InvalidRiskSetting(f"{key} must be a whole number")

        lo, hi = BOUNDS[key]
        if not (lo <= value <= hi):
            raise InvalidRiskSetting(f"{key} must be between {lo} and {hi}")


def update_settings(patch: dict, db=None) -> dict:
    """Validates the whole patch before writing anything, so a single bad
    field can't leave the row partially updated."""
    validate(patch)

    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = _get_or_create(db)
        for key, value in patch.items():
            setattr(row, key, int(value) if key in INT_FIELDS else value)
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def reset_settings(db=None) -> dict:
    return update_settings(dict(DEFAULTS), db=db)
