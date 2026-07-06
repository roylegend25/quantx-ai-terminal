"""Feature-attribution without the `shap` package.

XGBoost, LightGBM and CatBoost all implement TreeSHAP natively
(pred_contribs / pred_contrib / ShapValues) - these are exact SHAP values
from the library authors, not an approximation. For estimators without
native SHAP (Random Forest, VotingClassifier, Keras nets) we fall back to
sklearn permutation importance and say so: every payload carries a
`method` field so the UI never presents one attribution method as another.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SAMPLE_ROWS = 120  # per-row contributions kept for beeswarm plots


def _tree_shap_matrix(model, X: pd.DataFrame) -> np.ndarray | None:
    """Returns (n_rows, n_features) SHAP contributions (bias column
    stripped), or None when the model has no native TreeSHAP."""
    cls = type(model).__name__

    if cls == "XGBClassifier":
        import xgboost as xgb

        booster = model.get_booster()
        contribs = booster.predict(xgb.DMatrix(X), pred_contribs=True)
        return contribs[:, :-1]

    if cls == "LGBMClassifier":
        contribs = model.predict(X, pred_contrib=True)
        return np.asarray(contribs)[:, :-1]

    if cls == "CatBoostClassifier":
        from catboost import Pool

        contribs = model.get_feature_importance(Pool(X), type="ShapValues")
        return np.asarray(contribs)[:, :-1]

    return None


def shap_summary(model, X: pd.DataFrame, y: np.ndarray | None = None) -> dict:
    """Global attribution: either native TreeSHAP (mean |contribution| per
    feature + a row sample for beeswarm) or permutation importance."""
    feature_names = list(X.columns)
    matrix = _tree_shap_matrix(model, X)

    if matrix is not None:
        mean_abs = np.abs(matrix).mean(axis=0)
        order = np.argsort(-mean_abs)
        sample_idx = np.linspace(0, len(X) - 1, min(SAMPLE_ROWS, len(X))).astype(int)
        return {
            "method": "native_tree_shap",
            "features": [
                {
                    "feature": feature_names[i],
                    "mean_abs_shap": round(float(mean_abs[i]), 6),
                    "mean_shap": round(float(matrix[:, i].mean()), 6),
                }
                for i in order
            ],
            "sample": {
                "features": feature_names,
                "shap_rows": np.round(matrix[sample_idx], 5).tolist(),
                "value_rows": np.round(X.iloc[sample_idx].to_numpy(dtype=float), 5).tolist(),
            },
        }

    if y is None:
        return {"method": "unavailable", "reason": f"{type(model).__name__} has no native SHAP and no labels were provided for permutation importance", "features": []}

    from sklearn.inspection import permutation_importance

    result = permutation_importance(model, X, y, n_repeats=5, random_state=42, n_jobs=1)
    order = np.argsort(-result.importances_mean)
    return {
        "method": "permutation_importance",
        "features": [
            {
                "feature": feature_names[i],
                "importance": round(float(result.importances_mean[i]), 6),
                "std": round(float(result.importances_std[i]), 6),
            }
            for i in order
        ],
        "sample": None,
    }


def explain_row(model, row: pd.DataFrame) -> dict | None:
    """Per-prediction contributions for one feature vector (1-row frame).
    Native TreeSHAP models only - callers must handle None honestly."""
    matrix = _tree_shap_matrix(model, row)
    if matrix is None:
        return None
    contribs = matrix[0]
    feature_names = list(row.columns)
    order = np.argsort(-np.abs(contribs))
    return {
        "method": "native_tree_shap",
        "contributions": [
            {
                "feature": feature_names[i],
                "value": round(float(row.iloc[0, i]), 6),
                "shap": round(float(contribs[i]), 6),
            }
            for i in order
        ],
    }
