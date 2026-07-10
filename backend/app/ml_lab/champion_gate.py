"""Safety gate between the ML Champion and the live prediction engine.

The strategy ensemble (app/strategy/ensemble.py) is ALWAYS the fallback.
The Champion's probability is used only when every condition holds:

  1. champion inference is enabled in the lab settings (off by default -
     promoting a model must never silently change paper-trading behavior),
  2. a Champion exists in the mlops registry,
  3. its model file loads and exposes predict_proba (health),
  4. the current data-quality verdict is not "unreliable",
  5. every feature the model was trained on can be built from the live
     feature snapshot (no imputed zeros - a vector the model never saw
     would produce a confident-looking garbage probability).

Any condition failing returns used=False with the exact reason, and the
caller keeps the ensemble decision untouched.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ml_lab.settings_repo import get_settings
from app.mlops.model_registry import registry


@lru_cache(maxsize=4)
def _load_bundle(model_path: str):
    import joblib

    return joblib.load(model_path)


def clear_cache():
    _load_bundle.cache_clear()


def _champion_bundle(db: Session) -> tuple[dict | None, dict | None, str | None]:
    """(champion_row, bundle, reason_unusable)"""
    champion = registry.get_champion(db=db)
    if champion is None:
        return None, None, "No ML Champion has been promoted yet"
    path = champion.get("model_path")
    if not path or not Path(path).exists():
        return champion, None, f"Champion {champion['model_name']} {champion['version']} has no model file on disk"
    try:
        bundle = _load_bundle(path)
    except Exception as exc:
        return champion, None, f"Champion model file failed to load: {exc}"
    if not isinstance(bundle, dict) or "feature_names" not in bundle:
        return champion, None, "Champion artifact has an unrecognized format"
    if bundle.get("kind") != "sklearn" or not hasattr(bundle.get("model"), "predict_proba"):
        return champion, None, "Champion is not a probability classifier usable for live inference (sequence models are excluded)"
    return champion, bundle, None


def vector_from_features(features: dict, feature_names: list[str]) -> tuple[list[float] | None, list[str]]:
    """Builds the model input from the live compute_features() snapshot,
    using the same derivations the training dataset used. Returns
    (vector, missing_names); vector is None when anything is missing."""
    values: dict[str, float | None] = {}
    for name in feature_names:
        if name in features and features[name] is not None:
            values[name] = float(features[name])
        elif name == "volume_ratio":
            vol, sma = features.get("volume"), features.get("volume_sma20")
            values[name] = float(vol) / float(sma) if vol and sma else None
        elif name.startswith("regime_"):
            values[name] = 1.0 if features.get("regime") == name.removeprefix("regime_") else 0.0
        else:
            values[name] = None
    missing = [k for k, v in values.items() if v is None]
    if missing:
        return None, missing
    return [values[name] for name in feature_names], []


def status(db: Session | None = None) -> dict:
    """Config + health summary for the Champion tab (no prediction made)."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        enabled = bool(get_settings(db=db)["inference"]["champion_enabled"])
        champion, bundle, reason = _champion_bundle(db)
        healthy = bundle is not None
        if enabled and healthy:
            verdict = "Champion drives live predictions (strategy ensemble remains the fallback)"
        elif not enabled:
            verdict = "Champion inference is disabled - predictions come from the strategy ensemble"
        else:
            verdict = f"Falling back to the strategy ensemble: {reason}"
        return {
            "enabled": enabled,
            "champion_exists": champion is not None,
            "healthy": healthy,
            "reason": reason,
            "active": enabled and healthy,
            "verdict": verdict,
        }
    finally:
        if owns_session:
            db.close()


def maybe_predict(features: dict, data_quality: dict | None = None, db: Session | None = None) -> dict:
    """Returns {"used": True, p_up, direction, confidence, ...} when every
    gate passes, else {"used": False, "reason": ...}. Never raises.

    `expected` marks the not-used cases where the Champion was configured to
    drive predictions but couldn't (bad data, missing file, missing features):
    that is a *fallback* to the strategy ensemble, as opposed to the normal
    disabled/no-champion states where the ensemble is simply the engine."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        try:
            if not get_settings(db=db)["inference"]["champion_enabled"]:
                return {
                    "used": False,
                    "expected": False,
                    "reason": "Champion inference is disabled in the lab settings",
                }

            if data_quality is not None and data_quality.get("reliable") is False:
                return {
                    "used": False,
                    "expected": True,
                    "reason": data_quality.get("reason") or "Market data quality below the usable threshold",
                }

            champion, bundle, reason = _champion_bundle(db)
            if bundle is None:
                return {"used": False, "expected": champion is not None, "reason": reason}

            vector, missing = vector_from_features(features, bundle["feature_names"])
            if vector is None:
                return {"used": False, "expected": True, "reason": f"Live feature snapshot is missing {missing}"}

            p_up = float(bundle["model"].predict_proba([vector])[0][1])
            return {
                "used": True,
                "model_name": champion["model_name"],
                "algorithm": champion.get("algorithm") or champion["model_name"],
                "version": champion["version"],
                "model_id": champion["model_id"],
                "p_up": round(p_up, 4),
                "direction": "LONG" if p_up >= 0.5 else "SHORT",
                "confidence": round(abs(p_up - 0.5) * 200, 1),
            }
        except Exception as exc:  # a gate bug must never take down predictions
            return {"used": False, "expected": True, "reason": f"Champion gate error: {exc!r}"}
    finally:
        if owns_session:
            db.close()
