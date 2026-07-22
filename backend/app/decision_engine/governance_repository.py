"""Configuration-driven thresholds for the indicator poor-performance/star
rules (Bot Settings Part 6/10) - a singleton row, not scoped per-mode since
it governs the shared evaluator (app.decision_engine.indicator_performance),
not per-mode trading risk limits. Same read-fresh-every-call idiom as
app/risk/settings_repository.py.
"""
from datetime import datetime, timezone

from app.db.models import IndicatorGovernanceSettings
from app.db.session import SessionLocal

DEFAULTS = {
    "poor_performance_window": 10,
    "poor_performance_wrong_threshold": 7,
    "min_sample_for_poor_performance_check": 10,
    "status_change_cooldown_hours": 24.0,
    "star_min_shadow_samples": 20,
    "star_min_hit_rate": 0.65,
    "star_max_wrong_rate": 0.35,
    "star_max_mae_pct": 5.0,
    "star_recent_subwindow": 10,
    "data_quality_void_rate_threshold": 0.30,
}

BOUNDS = {
    "poor_performance_window": (5, 50),
    "poor_performance_wrong_threshold": (1, 50),
    "min_sample_for_poor_performance_check": (1, 50),
    "status_change_cooldown_hours": (0.0, 24 * 30),
    "star_min_shadow_samples": (5, 200),
    "star_min_hit_rate": (0.5, 1.0),
    "star_max_wrong_rate": (0.0, 0.5),
    "star_max_mae_pct": (0.1, 50.0),
    "star_recent_subwindow": (3, 100),
    "data_quality_void_rate_threshold": (0.0, 1.0),
}

INT_FIELDS = ("poor_performance_window", "poor_performance_wrong_threshold",
              "min_sample_for_poor_performance_check", "star_min_shadow_samples", "star_recent_subwindow")
FIELDS = tuple(DEFAULTS.keys())


class InvalidGovernanceSetting(ValueError):
    pass


def _row_to_dict(row: IndicatorGovernanceSettings) -> dict:
    return {**{f: getattr(row, f) for f in FIELDS}, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _get_or_create(db) -> IndicatorGovernanceSettings:
    row = db.get(IndicatorGovernanceSettings, 1)
    if not row:
        row = IndicatorGovernanceSettings(id=1, **DEFAULTS)
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


def update_settings(patch: dict, db=None) -> dict:
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise InvalidGovernanceSetting(f"Unknown governance setting: {key}")
        lo, hi = BOUNDS[key]
        if not (lo <= value <= hi):
            raise InvalidGovernanceSetting(f"{key} must be between {lo} and {hi}")

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
