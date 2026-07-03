"""Champion/challenger comparison and automatic promotion.

A challenger is promoted only when it clears every configured threshold in
app/core/config.py (accuracy, profit factor, Sharpe, drawdown, prediction
stability, latency) - "ALL configurable thresholds", per the Phase 15 spec,
not a majority vote.
"""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.mlops import drift_detector
from app.mlops.model_registry import registry

COMPARISON_FIELDS = [
    "val_accuracy",
    "train_accuracy",
    "win_rate",
    "sharpe_ratio",
    "profit_factor",
    "max_drawdown_pct",
    "latency_ms",
]


def _stability_score(db: Session | None = None) -> float | None:
    """1.0 = perfectly stable, 0.0 = fully drifted. Derived from the most
    recent prediction-drift check; None (no check run yet) is treated as
    "unknown" rather than "failing" by thresholds_met."""
    latest = drift_detector.latest_by_type(db=db).get("prediction")
    if not latest or latest.get("score") is None:
        return None
    return round(max(0.0, 1.0 - latest["score"]), 4)


def thresholds_met(model_id: str, db: Session | None = None) -> dict:
    model = registry.get_by_id(model_id, db=db)
    if model is None:
        return {"met": False, "reasons": [f"Model '{model_id}' not found"]}

    accuracy = model.get("val_accuracy") if model.get("val_accuracy") is not None else model.get("train_accuracy")
    stability = _stability_score(db=db)

    checks = {
        "accuracy": (accuracy, settings.champion_min_accuracy, "gte"),
        "profit_factor": (model.get("profit_factor"), settings.champion_min_profit_factor, "gte"),
        "sharpe_ratio": (model.get("sharpe_ratio"), settings.champion_min_sharpe, "gte"),
        "max_drawdown_pct": (model.get("max_drawdown_pct"), settings.champion_max_drawdown_pct, "lte"),
        "stability": (stability, settings.champion_min_stability, "gte"),
        "latency_ms": (model.get("latency_ms"), settings.champion_max_latency_ms, "lte"),
    }

    reasons = []
    detail = {}
    for name, (value, threshold, op) in checks.items():
        if value is None:
            detail[name] = {"value": None, "threshold": threshold, "passed": None}
            continue
        passed = value >= threshold if op == "gte" else value <= threshold
        detail[name] = {"value": value, "threshold": threshold, "passed": passed}
        if not passed:
            reasons.append(f"{name} {value} does not meet threshold {threshold} ({op})")

    return {"met": len(reasons) == 0, "reasons": reasons, "detail": detail}


def compare(challenger_id: str, champion_id: str | None = None, db: Session | None = None) -> dict:
    challenger = registry.get_by_id(challenger_id, db=db)
    if challenger is None:
        raise ValueError(f"Challenger model '{challenger_id}' not found")

    champion = registry.get_by_id(champion_id, db=db) if champion_id else registry.get_champion(
        model_name=challenger["model_name"], db=db
    )

    diff = {}
    for field in COMPARISON_FIELDS:
        challenger_value = challenger.get(field)
        champion_value = champion.get(field) if champion else None
        delta = (
            round(challenger_value - champion_value, 4)
            if challenger_value is not None and champion_value is not None
            else None
        )
        diff[field] = {"champion": champion_value, "challenger": challenger_value, "delta": delta}

    return {"champion": champion, "challenger": challenger, "diff": diff}


def maybe_promote(challenger_id: str, db: Session | None = None) -> dict:
    result = thresholds_met(challenger_id, db=db)
    if not result["met"]:
        return {"promoted": False, **result}

    promoted = registry.promote_to_champion(challenger_id, db=db)
    return {"promoted": True, "model": promoted, **result}
