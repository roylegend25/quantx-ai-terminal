"""Editable risk limits, persisted in SQLite/Postgres, one row per scope.

Scoped by `scope` ("paper" | "binance_real") - Paper and Binance Real are
two independent rows, never a shared one (see
app/db/risk_settings_scope_migration.py for how the old singleton row was
split). Read fresh from the DB on every call rather than cached in memory,
so a PUT from Bot Settings takes effect on the very next prediction/
scheduler cycle without a restart - the whole point of moving these out of
the static, env-only app/core/config.py Settings object.

This module must never import app.trading.modes - a settings save/copy/
reset can never carry a live-execution side effect, by construction of
which modules import what, not just by convention.
"""
from datetime import datetime, timezone

from app.core.config import settings as env_settings
from app.db.models import RiskSettings, RiskSettingsAudit
from app.db.session import SessionLocal

SCOPES = ("paper", "binance_real")

# Doubles as the "Balanced" preset and the reset-to-defaults target.
DEFAULTS = {
    "min_confidence_to_trade": 0.60,
    "min_point_margin": env_settings.active_drive_min_point_margin,
    "min_total_evidence": env_settings.active_drive_min_total_evidence,
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
    "min_point_margin": (0.0, 50.0),
    "min_total_evidence": (0.0, 100.0),
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
# Fields where a LOWER value is a riskier configuration - a lowering PUT on
# scope="binance_real" for any of these requires explicit confirmation (see
# app/risk/threshold_risk.py).
LOWER_IS_RISKIER = ("min_confidence_to_trade", "min_point_margin", "min_total_evidence")

FIELDS = tuple(DEFAULTS.keys())
# Fields copied verbatim by copy_settings(); excludes identity/bookkeeping columns.
COPYABLE_FIELDS = FIELDS


class InvalidRiskSetting(ValueError):
    pass


class InvalidScope(ValueError):
    pass


def _validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise InvalidScope(f"Unknown risk settings scope: {scope!r}; must be one of {SCOPES}")


def _row_to_dict(row: RiskSettings) -> dict:
    return {
        **{field: getattr(row, field) for field in FIELDS},
        "scope": row.scope,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _get_or_create(db, scope: str) -> RiskSettings:
    _validate_scope(scope)
    row = db.query(RiskSettings).filter_by(scope=scope).first()
    if not row:
        row = RiskSettings(scope=scope, version=1, **DEFAULTS)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_settings(scope: str = "paper", db=None) -> dict:
    _validate_scope(scope)
    owns_session = db is None
    db = db or SessionLocal()
    try:
        return _row_to_dict(_get_or_create(db, scope))
    finally:
        if owns_session:
            db.close()


def get_user_settings(user_id: str, scope: str = "paper", db=None) -> dict:
    """Ownership-aware read boundary for automated sizing.

    Current installations have one configured trading account per scope; the
    returned owner stamp prevents that row from being reused under a
    different authority identity inside the sizing pipeline. Callers with a
    `mode` in hand (PAPER vs BINANCE_LIVE/BINANCE_TESTNET) should derive
    `scope` from it rather than relying on the "paper" default.
    """
    return {**get_settings(scope, db), "risk_policy_owner": str(user_id)}


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


def _write_audit_rows(db, *, scope: str, configuration_version: int, changed_by: str,
                       reason: str | None, change_kind: str, diffs: list) -> None:
    now = datetime.now(timezone.utc)
    for field, previous_value, new_value in diffs:
        db.add(RiskSettingsAudit(
            scope=scope, configuration_version=configuration_version, field=field,
            previous_value=previous_value, new_value=new_value, changed_by=changed_by,
            reason=reason, change_kind=change_kind, created_at=now,
        ))


def update_settings(patch: dict, scope: str = "paper", *, changed_by: str = "system",
                     reason: str | None = None, db=None) -> dict:
    """Validates the whole patch before writing anything, so a single bad
    field can't leave the row partially updated. Writes one RiskSettingsAudit
    row per changed field and bumps the scope's optimistic-concurrency
    version. Never imports or calls anything in app.trading.modes."""
    validate(patch)
    _validate_scope(scope)

    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = _get_or_create(db, scope)
        diffs = []
        for key, value in patch.items():
            coerced = int(value) if key in INT_FIELDS else value
            previous = getattr(row, key)
            if previous != coerced:
                diffs.append((key, previous, coerced))
            setattr(row, key, coerced)
        if diffs:
            row.version = (row.version or 1) + 1
            row.updated_at = datetime.now(timezone.utc)
            _write_audit_rows(db, scope=scope, configuration_version=row.version, changed_by=changed_by,
                               reason=reason, change_kind="update", diffs=diffs)
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def reset_settings(scope: str = "paper", *, changed_by: str = "system", reason: str | None = None, db=None) -> dict:
    return update_settings(dict(DEFAULTS), scope=scope, changed_by=changed_by, reason=reason, db=db)


def copy_settings(from_scope: str, to_scope: str, *, changed_by: str, reason: str | None = None, db=None) -> dict:
    """Full-field copy from one scope to another (Bot Settings' "Copy Paper
    Settings to Binance Real" / "Copy Binance Real Settings to Paper"
    buttons). Never touches live-execution state - this function has no
    knowledge of app.trading.modes, binance_live_enabled, or any lease/lock
    concept; it only ever copies the numeric/boolean risk fields listed in
    COPYABLE_FIELDS."""
    _validate_scope(from_scope)
    _validate_scope(to_scope)
    if from_scope == to_scope:
        raise InvalidScope("from_scope and to_scope must differ")

    owns_session = db is None
    db = db or SessionLocal()
    try:
        source = _get_or_create(db, from_scope)
        target = _get_or_create(db, to_scope)
        patch = {field: getattr(source, field) for field in COPYABLE_FIELDS}
        diffs = []
        for key, value in patch.items():
            previous = getattr(target, key)
            if previous != value:
                diffs.append((key, previous, value))
            setattr(target, key, value)
        if diffs:
            target.version = (target.version or 1) + 1
            target.updated_at = datetime.now(timezone.utc)
            change_kind = "copy_from_paper" if from_scope == "paper" else "copy_from_binance_real"
            _write_audit_rows(db, scope=to_scope, configuration_version=target.version, changed_by=changed_by,
                               reason=reason, change_kind=change_kind, diffs=diffs)
        db.commit()
        db.refresh(target)
        return _row_to_dict(target)
    finally:
        if owns_session:
            db.close()


def get_audit_history(scope: str = "paper", limit: int = 200, db=None) -> list:
    _validate_scope(scope)
    owns_session = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(RiskSettingsAudit)
            .filter_by(scope=scope)
            .order_by(RiskSettingsAudit.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id, "scope": r.scope, "configuration_version": r.configuration_version,
                "field": r.field, "previous_value": r.previous_value, "new_value": r.new_value,
                "changed_by": r.changed_by, "reason": r.reason, "change_kind": r.change_kind,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        if owns_session:
            db.close()
