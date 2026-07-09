"""Walk-forward (out-of-sample) validation for tabular challengers.

Expanding-window scheme on the chronologically-ordered dataset: the frame's
tail is cut into `n_folds` sequential slices, and for each slice a FRESH
estimator (same algorithm, same hyperparameters) is fitted on everything
strictly before it and scored on the slice alone. No fold ever sees its own
future - this is the same discipline as the live deployment, repeated
n_folds times, so the mean fold accuracy is a far harder number to get
lucky on than one train/test split.

Sequence (Keras) models are skipped: n_folds full re-fits on this 2-vCPU
host would take tens of minutes and starve the trading backend. The
returned dict says so explicitly - oos_accuracy stays NULL and the
promotion rules treat that as "unevaluable", which cannot auto-promote.
"""

from __future__ import annotations

import numpy as np

from app.ml_lab.algorithms import SEQUENCE_ALGORITHMS, build_estimator

DEFAULT_FOLDS = 4

# each evaluation slice must hold at least this many rows or its accuracy
# is noise; folds below it are folded into the report as skipped
MIN_FOLD_ROWS = 30

# the first fit needs a real training window, not a handful of warmup rows
MIN_TRAIN_ROWS = 150


def run(algorithm: str, hyperparameters: dict, X, y, forward_returns=None, n_folds: int = DEFAULT_FOLDS) -> dict:
    """X: full chronological feature frame, y: labels, forward_returns:
    optional aligned realized returns (adds per-fold win rate). Returns
    {available, folds, mean_accuracy, std_accuracy, ...}."""
    if algorithm in SEQUENCE_ALGORITHMS:
        return {
            "available": False,
            "reason": (
                "Walk-forward re-fits the model once per fold; doing that for a Keras sequence "
                "model on this CPU-only host would take too long to run inside a training job. "
                "Sequence challengers therefore cannot auto-promote and need a manual decision."
            ),
        }

    n = len(X)
    eval_start = MIN_TRAIN_ROWS
    if n - eval_start < n_folds * MIN_FOLD_ROWS:
        return {
            "available": False,
            "reason": (
                f"Dataset too small for walk-forward: {n} rows leave less than "
                f"{n_folds}x{MIN_FOLD_ROWS} evaluable rows after the {MIN_TRAIN_ROWS}-row initial window"
            ),
        }

    bounds = np.linspace(eval_start, n, n_folds + 1, dtype=int)
    folds = []
    for i in range(n_folds):
        start, end = int(bounds[i]), int(bounds[i + 1])
        if end - start < MIN_FOLD_ROWS:
            continue
        estimator = build_estimator(algorithm, hyperparameters)
        estimator.fit(X.iloc[:start], y[:start])
        y_score = estimator.predict_proba(X.iloc[start:end])[:, 1]
        y_pred = (y_score >= 0.5).astype(int)
        y_true = y[start:end]
        fold = {
            "fold": i + 1,
            "train_rows": start,
            "test_rows": end - start,
            "accuracy": round(float((y_pred == y_true).mean()), 4),
        }
        if forward_returns is not None:
            # directional win rate on threshold-crossing signals, same
            # definition as metrics_ext.trading_simulation's entry rule
            signals = np.abs(y_score - 0.5) >= 0.05
            if signals.sum() > 0:
                returns = np.asarray(forward_returns[start:end], dtype=float)
                gains = np.where(y_score >= 0.5, returns, -returns)[signals]
                fold["signals"] = int(signals.sum())
                fold["win_rate"] = round(float((gains > 0).mean() * 100), 2)
        folds.append(fold)

    if not folds:
        return {"available": False, "reason": "No fold had enough rows to evaluate"}

    accuracies = [f["accuracy"] for f in folds]
    return {
        "available": True,
        "n_folds": len(folds),
        "folds": folds,
        "mean_accuracy": round(float(np.mean(accuracies)), 4),
        "std_accuracy": round(float(np.std(accuracies)), 4),
        "worst_fold_accuracy": round(float(np.min(accuracies)), 4),
    }
