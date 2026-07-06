"""Per-prediction explainability.

Two real sources, clearly labeled:
  1. The rule ensemble's own reasoning - strategy votes, adaptive weights
     and the decision reason are stored verbatim on every PredictionFeature
     row at prediction time. Always available.
  2. ML-champion attribution - when an AI-lab-trained tree model holds
     Champion status, its native TreeSHAP contributions for this row's
     stored feature vector. Only offered when the model file actually
     loads and supports it; otherwise the response says exactly why not.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import PredictionFeature
from app.db.session import SessionLocal
from app.ml_lab import shap_native
from app.mlops.model_registry import registry


def _load_champion_bundle(db: Session) -> tuple[dict | None, dict | None, str | None]:
    """(champion_row, loaded_bundle, unavailable_reason)"""
    champion = registry.get_champion(db=db)
    if champion is None:
        return None, None, "No ML Champion has been promoted yet - train and promote a model in the AI Lab"
    path = champion.get("model_path")
    if not path or not Path(path).exists():
        return champion, None, f"Champion {champion['model_name']} {champion['version']} has no loadable model file"
    try:
        bundle = joblib.load(path)
    except Exception as exc:
        return champion, None, f"Champion model file failed to load: {exc}"
    if not isinstance(bundle, dict) or bundle.get("kind") != "sklearn":
        return champion, None, "Champion is a sequence model - per-feature TreeSHAP attribution does not apply to it"
    return champion, bundle, None


def _feature_vector(row: PredictionFeature, feature_names: list[str]) -> pd.DataFrame | None:
    tech = row.technical_features or {}
    values = {}
    for name in feature_names:
        if name in tech and tech[name] is not None:
            values[name] = float(tech[name])
        elif name == "volume_ratio":
            vol, sma = tech.get("volume"), tech.get("volume_sma20")
            values[name] = float(vol) / float(sma) if vol and sma else None
        elif name.startswith("regime_"):
            values[name] = 1.0 if tech.get("regime") == name.removeprefix("regime_") else 0.0
        else:
            values[name] = None
    if any(v is None for v in values.values()):
        missing = [k for k, v in values.items() if v is None]
        # honest failure: a vector with imputed zeros would attribute
        # importance to values the model never saw
        raise LookupError(f"Stored feature snapshot is missing {missing}")
    return pd.DataFrame([values], columns=feature_names)


def explain_prediction(prediction_id: int | None = None, symbol: str | None = None, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(PredictionFeature)
        if prediction_id is not None:
            row = query.filter(PredictionFeature.id == prediction_id).first()
        else:
            if symbol:
                query = query.filter(PredictionFeature.symbol == symbol.upper())
            row = query.order_by(PredictionFeature.timestamp.desc()).first()

        if row is None:
            return {"available": False, "reason": "No stored prediction matches this request"}

        strategies = row.adaptive_strategy_weights or {}
        base = {
            "available": True,
            "prediction_id": row.id,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "direction": row.direction,
            "confidence": row.confidence,
            "regime": row.regime,
            "decision_reason": row.decision_reason,
            "strategy_weights": strategies,
            "probability_distribution": {
                "up": round(50 + (row.confidence or 0) / 2, 1) if row.direction == "LONG" else round(50 - (row.confidence or 0) / 2, 1),
                "down": None,  # filled below
            },
            "technical_snapshot": row.technical_features,
        }
        base["probability_distribution"]["down"] = round(100 - base["probability_distribution"]["up"], 1)

        champion, bundle, reason = _load_champion_bundle(db)
        if bundle is None:
            base["ml_attribution"] = {"available": False, "reason": reason}
            return base

        try:
            vector = _feature_vector(row, bundle["feature_names"])
        except LookupError as exc:
            base["ml_attribution"] = {"available": False, "reason": str(exc)}
            return base

        model = bundle["model"]
        contribs = shap_native.explain_row(model, vector)
        proba = None
        if hasattr(model, "predict_proba"):
            proba = float(model.predict_proba(vector)[0, 1])

        if contribs is None:
            base["ml_attribution"] = {
                "available": False,
                "reason": f"Champion algorithm {type(model).__name__} has no native TreeSHAP support",
            }
        else:
            top = contribs["contributions"][:8]
            base["ml_attribution"] = {
                "available": True,
                "champion": {"model_name": champion["model_name"], "version": champion["version"]},
                "method": contribs["method"],
                "p_up": round(proba, 4) if proba is not None else None,
                "top_reasons": [
                    f"{c['feature']} = {c['value']:g} pushed {'UP' if c['shap'] > 0 else 'DOWN'} ({c['shap']:+.4f})"
                    for c in top
                ],
                "contributions": contribs["contributions"],
            }
        return base
    finally:
        if owns_session:
            db.close()
