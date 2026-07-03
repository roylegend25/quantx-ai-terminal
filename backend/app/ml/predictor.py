"""Loads a trained challenger model artifact for offline evaluation.

This is intentionally never imported by the live prediction path
(app/api/prediction.py, app/strategy/ensemble.py) - the adaptive ensemble
stays the only model driving real trades. It exists so app/ml/backtest_models.py
(and ad-hoc analysis) can score a challenger against held-out data.
"""

import joblib

from app.ml.model_registry import registry


class ModelPredictor:
    def __init__(self, model_name: str):
        record = registry.get_latest(model_name)
        if record is None or not record.get("model_path"):
            raise LookupError(f"No trained artifact found for model '{model_name}'")

        bundle = joblib.load(record["model_path"])
        self.model = bundle["model"]
        self.feature_names = bundle["feature_names"]
        self.model_name = model_name
        self.version = record["version"]

    def predict_proba_up(self, feature_row: dict) -> float:
        vector = [[float(feature_row.get(name) or 0.0) for name in self.feature_names]]
        return float(self.model.predict_proba(vector)[0][1])

    def predict(self, feature_row: dict) -> dict:
        probability_up = self.predict_proba_up(feature_row)
        direction = "LONG" if probability_up >= 0.5 else "SHORT"
        return {
            "model_name": self.model_name,
            "version": self.version,
            "direction": direction,
            "probability_up": round(probability_up * 100, 2),
            "confidence": round(abs(probability_up - 0.5) * 200, 2),
        }


def load_predictor(model_name: str) -> ModelPredictor:
    return ModelPredictor(model_name)
