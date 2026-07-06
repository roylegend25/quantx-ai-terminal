"""Extended evaluation artifacts for the AI Model Lab: confusion matrix,
ROC / PR / calibration curves, lift & gain by decile, and a simple
long/short trading simulation on the holdout slice. Everything is computed
from real holdout predictions - these functions never see training rows.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    log_loss as sk_log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

CURVE_POINTS = 60  # downsampled for the UI - full curves are overkill as JSON


def _downsample(xs: np.ndarray, ys: np.ndarray, extra: np.ndarray | None = None, points: int = CURVE_POINTS):
    if len(xs) <= points:
        idx = np.arange(len(xs))
    else:
        idx = np.unique(np.linspace(0, len(xs) - 1, points).astype(int))
    out = []
    for i in idx:
        item = {"x": round(float(xs[i]), 4), "y": round(float(ys[i]), 4)}
        if extra is not None and i < len(extra):
            # sklearn's roc_curve reports +inf as its first threshold -
            # not JSON-encodable, and meaningless to plot
            t = float(extra[i])
            item["t"] = round(t, 4) if np.isfinite(t) else None
        out.append(item)
    return out


def confusion(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    tn = int(np.sum((y_true == 0) & (y_pred == 0)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "total": int(len(y_true)),
    }


def roc_points(y_true: np.ndarray, y_score: np.ndarray) -> list[dict] | None:
    if len(np.unique(y_true)) < 2:
        return None
    fpr, tpr, thresholds = roc_curve(y_true, y_score)
    return _downsample(fpr, tpr, thresholds)


def pr_points(y_true: np.ndarray, y_score: np.ndarray) -> list[dict] | None:
    if len(np.unique(y_true)) < 2:
        return None
    precision, recall, _ = precision_recall_curve(y_true, y_score)
    return _downsample(recall[::-1], precision[::-1])


def calibration_points(y_true: np.ndarray, y_score: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_score >= lo) & (y_score < hi if i < bins - 1 else y_score <= hi)
        count = int(mask.sum())
        out.append(
            {
                "bin_mid": round(float((lo + hi) / 2), 3),
                "count": count,
                "predicted": round(float(y_score[mask].mean()), 4) if count else None,
                "actual": round(float(y_true[mask].mean()), 4) if count else None,
            }
        )
    return out


def expected_calibration_error(y_true: np.ndarray, y_score: np.ndarray, bins: int = 10) -> float | None:
    points = calibration_points(y_true, y_score, bins)
    n = len(y_true)
    if n == 0:
        return None
    ece = 0.0
    for p in points:
        if p["count"] and p["predicted"] is not None and p["actual"] is not None:
            ece += (p["count"] / n) * abs(p["predicted"] - p["actual"])
    return round(float(ece), 4)


def lift_gain(y_true: np.ndarray, y_score: np.ndarray, deciles: int = 10) -> dict:
    """Rows sorted by predicted score, split into deciles: what share of all
    actual positives each cumulative slice captures (gain) and how much
    better than random each decile is (lift)."""
    order = np.argsort(-y_score)
    y_sorted = y_true[order]
    n = len(y_sorted)
    total_pos = max(1, int(y_sorted.sum()))
    base_rate = total_pos / n if n else 0.0

    rows = []
    cum_pos = 0
    for d in range(deciles):
        lo = int(n * d / deciles)
        hi = int(n * (d + 1) / deciles)
        slice_pos = int(y_sorted[lo:hi].sum())
        cum_pos += slice_pos
        slice_n = hi - lo
        rows.append(
            {
                "decile": d + 1,
                "count": slice_n,
                "positives": slice_pos,
                "lift": round((slice_pos / slice_n) / base_rate, 3) if slice_n and base_rate else None,
                "cumulative_gain_pct": round(cum_pos / total_pos * 100, 2),
                "population_pct": round(hi / n * 100, 2) if n else 0.0,
            }
        )
    return {"base_rate": round(base_rate, 4), "deciles": rows}


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_score: np.ndarray | None) -> dict:
    metrics = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "auc": None,
        "log_loss": None,
        "calibration_error": None,
    }
    if y_score is not None and len(np.unique(y_true)) > 1:
        try:
            metrics["auc"] = round(float(roc_auc_score(y_true, y_score)), 4)
        except ValueError:
            pass
        try:
            metrics["log_loss"] = round(float(sk_log_loss(y_true, y_score, labels=[0, 1])), 4)
        except ValueError:
            pass
        metrics["calibration_error"] = expected_calibration_error(y_true, y_score)
    return metrics


def trading_simulation(
    y_score: np.ndarray,
    forward_returns: np.ndarray,
    long_threshold: float = 0.55,
    short_threshold: float = 0.45,
) -> dict:
    """What the holdout slice would have returned if traded: long when
    P(up) > long_threshold, short when P(up) < short_threshold, flat
    otherwise. One position per bar, no compounding/fees - a comparable
    signal-quality score across models, not a full backtest (the Research
    Lab owns those)."""
    signals = np.zeros(len(y_score))
    signals[y_score > long_threshold] = 1
    signals[y_score < short_threshold] = -1
    trade_mask = signals != 0
    trade_returns = signals[trade_mask] * forward_returns[trade_mask]

    n_trades = int(trade_mask.sum())
    if n_trades == 0:
        return {
            "trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "sharpe_ratio": None,
            "max_drawdown_pct": None,
            "total_return_pct": None,
            "expectancy_pct": None,
            "reason": f"No holdout probability left the {short_threshold}-{long_threshold} no-trade band",
        }

    wins = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns < 0]
    gross_win = float(wins.sum())
    gross_loss = abs(float(losses.sum()))

    equity = np.cumprod(1 + trade_returns)
    peak = np.maximum.accumulate(equity)
    max_dd = float(((peak - equity) / peak).max()) if len(equity) else 0.0

    std = float(trade_returns.std())
    sharpe = float(trade_returns.mean() / std * np.sqrt(min(n_trades, 252))) if std > 0 else None

    return {
        "trades": n_trades,
        "win_rate": round(len(wins) / n_trades * 100, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "expectancy_pct": round(float(trade_returns.mean()) * 100, 4),
        "long_threshold": long_threshold,
        "short_threshold": short_threshold,
    }
