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
        "min_win_rate": 50.0,
        "min_sharpe": 0.3,
        "min_profit_factor": 1.05,
        "min_trades": 20,
        "max_drawdown_pct": 30.0,
        "min_confidence": 0.5,
        # holdout slice must be big enough to mean something
        "min_test_samples": 50,
        # mean walk-forward (out-of-sample) accuracy across expanding-window
        # folds - the strongest guard against a lucky single split
        "min_oos_accuracy": 0.50,
        # challenger accuracy must beat the current Champion's by this many
        # percent (relative); 0 = must simply not be worse
        "champion_margin_pct": 1.0,
    },
    "retraining": {
        "schedule": "manual",  # manual | every_6_hours | daily | weekly
        # algorithms a scheduled/train-now batch attempts; unavailable ones
        # (missing lib, no CUDA, low RAM) are skipped with the honest reason
        "schedule_algorithms": ["xgboost", "lightgbm", "catboost", "random_forest", "ensemble_ml"],
        "drift_trigger_enabled": True,
        "drift_algorithms": ["xgboost", "lightgbm"],
        # bookkeeping written by the scheduler/train-now path, not the user
        "last_scheduled_run": None,
    },
    "training_gate": {
        # pre-training health gate thresholds (app/ml_lab/preflight.py)
        "min_dataset_rows": 500,
        "min_quality_score": 60.0,
        "max_gap_bars": 12,
        "min_free_ram_mb": 400,
        "min_free_disk_mb": 500,
    },
    "inference": {
        # When true AND a healthy Champion exists AND data quality is OK,
        # the live prediction engine uses the ML Champion's probability;
        # otherwise it falls back to the strategy ensemble (always the
        # fallback, never removed). Off by default so promoting a model
        # never silently changes paper-trading behavior - flip it in the
        # Champion tab when ready.
        "champion_enabled": False,
    },
}

# accepted values for retraining.schedule ("monthly" kept for rows saved by
# the earlier UI; it still works)
SCHEDULE_CHOICES = ["manual", "every_6_hours", "daily", "weekly", "monthly"]


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
            if section == "retraining":
                schedule = values.get("schedule")
                if schedule is not None and schedule not in SCHEDULE_CHOICES:
                    raise ValueError(f"Unknown schedule '{schedule}'. Valid: {SCHEDULE_CHOICES}")
                algos = values.get("schedule_algorithms")
                if algos is not None:
                    from app.ml_lab.algorithms import ALGORITHMS

                    unknown = [a for a in algos if a not in ALGORITHMS]
                    if unknown:
                        raise ValueError(f"Unknown algorithms {unknown}. Valid: {list(ALGORITHMS)}")
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
    avg_conf = model.get("avg_confidence")

    checks = [
        ("accuracy", accuracy, rules["min_accuracy"], "gte"),
        ("win_rate", model.get("win_rate"), rules["min_win_rate"], "gte"),
        ("sharpe_ratio", model.get("sharpe_ratio"), rules["min_sharpe"], "gte"),
        ("profit_factor", model.get("profit_factor"), rules["min_profit_factor"], "gte"),
        ("trades", model.get("total_trades"), rules["min_trades"], "gte"),
        ("max_drawdown_pct", model.get("max_drawdown_pct"), rules["max_drawdown_pct"], "lte"),
        ("avg_confidence", avg_conf, rules["min_confidence"], "gte"),
        ("test_samples", model.get("test_samples"), rules["min_test_samples"], "gte"),
        ("oos_accuracy", model.get("oos_accuracy"), rules["min_oos_accuracy"], "gte"),
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

    # must beat the reigning Champion by champion_margin_pct (relative, on
    # accuracy). No Champion, or comparing the Champion to itself, passes -
    # there is nothing to beat.
    margin = float(rules.get("champion_margin_pct", 0.0))
    champion = _current_champion(db=db)
    if champion is None or champion.get("model_id") == model.get("model_id"):
        detail["beats_champion"] = {"value": None, "threshold": margin, "passed": True,
                                    "note": "No reigning Champion to beat"}
    else:
        champ_acc = champion.get("val_accuracy") if champion.get("val_accuracy") is not None else champion.get("train_accuracy")
        if champ_acc is None:
            detail["beats_champion"] = {"value": None, "threshold": margin, "passed": True,
                                        "note": "Champion has no recorded accuracy to compare against"}
        elif accuracy is None:
            detail["beats_champion"] = {"value": None, "threshold": margin, "passed": False}
            failures.append("beats_champion: challenger has no accuracy to compare")
        else:
            required = champ_acc * (1 + margin / 100.0)
            passed = accuracy >= required
            detail["beats_champion"] = {
                "value": round(float(accuracy), 4),
                "threshold": round(float(required), 4),
                "champion_accuracy": round(float(champ_acc), 4),
                "margin_pct": margin,
                "passed": passed,
            }
            if not passed:
                failures.append(
                    f"beats_champion: accuracy {accuracy:.4f} does not beat Champion "
                    f"{champ_acc:.4f} by {margin}% (needs >= {required:.4f})"
                )

    return {
        "auto_promote_enabled": bool(rules["auto_promote"]),
        "met": len(failures) == 0,
        "failures": failures,
        "detail": detail,
    }


def _current_champion(db: Session | None = None) -> dict | None:
    from app.mlops.model_registry import registry

    return registry.get_champion(db=db)
