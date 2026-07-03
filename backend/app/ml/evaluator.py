"""Shared binary-classification evaluation used by every training script
(app/ml/train_*.py) and by the champion backtest (app/ml/backtest_models.py),
so the adaptive ensemble and every ML challenger are scored the same way."""

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    log_loss as sk_log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

CALIBRATION_BINS = 5


def _calibration_curve(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = CALIBRATION_BINS) -> list[dict]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    curve = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_score >= lo) & (y_score <= hi if i == n_bins - 1 else y_score < hi)
        count = int(mask.sum())
        curve.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "count": count,
                "predicted_avg": round(float(y_score[mask].mean()), 4) if count else None,
                "actual_rate": round(float(y_true[mask].mean()), 4) if count else None,
            }
        )
    return curve


def evaluate_binary(y_true, y_pred, y_score=None) -> dict:
    """y_true/y_pred are 0/1 labels. y_score (optional) is the predicted
    probability of class 1, used for AUC, log loss, and calibration."""
    y_true = np.asarray(list(y_true), dtype=int)
    y_pred = np.asarray(list(y_pred), dtype=int)

    metrics = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "auc": None,
        "log_loss": None,
        "calibration": None,
    }

    if y_score is not None and len(np.unique(y_true)) > 1:
        y_score = np.asarray(list(y_score), dtype=float)
        try:
            metrics["auc"] = round(float(roc_auc_score(y_true, y_score)), 4)
        except ValueError:
            metrics["auc"] = None
        try:
            metrics["log_loss"] = round(float(sk_log_loss(y_true, y_score, labels=[0, 1])), 4)
        except ValueError:
            metrics["log_loss"] = None
        metrics["calibration"] = _calibration_curve(y_true, y_score)

    return metrics
