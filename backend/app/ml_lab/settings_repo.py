"""Dashboard-editable AI-lab configuration (singleton MLLabSettings row):
automatic-promotion thresholds and the retraining schedule / drift trigger.
Mirrors the app/risk/settings_repository.py pattern - read dynamically by
the training runner and the mlops scheduler so a PUT changes behavior on
the next job/cycle without a redeploy.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import MLLabSettings
from app.db.session import SessionLocal

DEFAULTS = {
    "promotion": {
        # Auto-promotion happens only when EVERY threshold passes. Values
        # chosen to be meaningful for the market-history dataset: 52%
        # accuracy is above coin-flip on next-bar direction, and the trade
        # gates stop a model that is accurate but untradeable.
        "auto_promote": True,
        "min_accuracy": 0.52,
        "min_sharpe": 0.3,
        "min_profit_factor": 1.05,
        "min_trades": 20,
        "max_drawdown_pct": 30.0,
        "min_confidence": 0.5,
    },
    "retraining": {
        "schedule": "manual",  # manual | daily | weekly | monthly
        "drift_trigger_enabled": True,
        "drift_algorithms": ["xgboost", "lightgbm"],
    },
}


def _merged(data: dict | None) -> dict:
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for section, values in (data or {}).items():
        if section in out and isinstance(values, dict):
            out[section].update(values)
    return out


def get_settings(db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.get(MLLabSettings, 1)
        if row is None:
            row = MLLabSettings(id=1, data=DEFAULTS)
            db.add(row)
            db.commit()
            db.refresh(row)
        return _merged(row.data)
    finally:
        if owns_session:
            db.close()


def update_settings(patch: dict, db: Session | None = None) -> dict:
    """Shallow-merges {section: {key: value}} into the stored settings.
    Unknown sections/keys are rejected so a typo'd field never silently
    configures nothing."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        current = get_settings(db=db)
        for section, values in patch.items():
            if section not in DEFAULTS:
                raise ValueError(f"Unknown settings section '{section}'. Valid: {list(DEFAULTS)}")
            if not isinstance(values, dict):
                raise ValueError(f"Section '{section}' must be an object")
            for key in values:
                if key not in DEFAULTS[section]:
                    raise ValueError(f"Unknown setting '{section}.{key}'. Valid: {list(DEFAULTS[section])}")
            current[section].update(values)

        row = db.get(MLLabSettings, 1)
        row.data = current
        db.commit()
        return _merged(row.data)
    finally:
        if owns_session:
            db.close()


def evaluate_promotion_rules(model: dict, db: Session | None = None) -> dict:
    """Applies the configured thresholds to a registry-row dict. Missing
    metric values fail their check explicitly (reason recorded) rather than
    passing silently - an unevaluable model must not auto-promote."""
    rules = get_settings(db=db)["promotion"]

    accuracy = model.get("val_accuracy") if model.get("val_accuracy") is not None else model.get("train_accuracy")
    win_rate = model.get("win_rate")
    avg_conf = model.get("avg_confidence")

    checks = [
        ("accuracy", accuracy, rules["min_accuracy"], "gte"),
        ("sharpe_ratio", model.get("sharpe_ratio"), rules["min_sharpe"], "gte"),
        ("profit_factor", model.get("profit_factor"), rules["min_profit_factor"], "gte"),
        ("trades", model.get("total_trades"), rules["min_trades"], "gte"),
        ("max_drawdown_pct", model.get("max_drawdown_pct"), rules["max_drawdown_pct"], "lte"),
        ("avg_confidence", avg_conf, rules["min_confidence"], "gte"),
    ]

    detail = {}
    failures = []
    for name, value, threshold, op in checks:
        if value is None:
            detail[name] = {"value": None, "threshold": threshold, "passed": False}
            failures.append(f"{name} is unavailable for this model (needs >= {threshold})" if op == "gte" else f"{name} is unavailable for this model (needs <= {threshold})")
            continue
        passed = value >= threshold if op == "gte" else value <= threshold
        detail[name] = {"value": round(float(value), 4), "threshold": threshold, "passed": passed}
        if not passed:
            failures.append(f"{name}={value:.4f} fails threshold ({'>=' if op == 'gte' else '<='} {threshold})")

    _ = win_rate  # displayed by the UI; not itself a promotion gate
    return {
        "auto_promote_enabled": bool(rules["auto_promote"]),
        "met": len(failures) == 0,
        "failures": failures,
        "detail": detail,
    }
