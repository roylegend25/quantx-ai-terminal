"""Extended drift monitoring on top of app/mlops/drift_detector.py.

Adds two statistical methods (KL divergence, Wasserstein distance) and two
drift types (data drift: aggregate shift across every technical feature;
concept drift: decay in realized directional accuracy over time) to the
existing PSI/KS feature/prediction/market checks. All results persist as
MLOpsDriftRecord rows - same table, new drift_type/method values - so
history charts read one series.

Status bands: healthy (score <= threshold), warning (<= 1.5x threshold),
critical (beyond). A check that can't compute (too few samples) reports
score=None and status "no_data" with the sample counts - never a fake
healthy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from scipy import stats
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MLOpsDriftRecord, PredictionFeature
from app.db.session import SessionLocal
from app.mlops.drift_detector import (
    MIN_SAMPLES,
    _persist,
    _split_window,
    check_feature_drift,
    check_market_drift,
    check_prediction_drift,
    ks_test,
    population_stability_index,
)

DRIFT_TYPES = ["feature", "prediction", "market", "data", "concept"]

# Concept drift threshold is an accuracy DROP (fraction), not a
# distribution score - a 0.15 drop in rolling hit rate is a regime change
# worth retraining on.
CONCEPT_DRIFT_THRESHOLD = 0.15

TRACKED_FEATURES = ["rsi", "atr", "macd_hist", "bb_width", "realized_volatility", "volume"]


def kl_divergence(expected, actual, bins: int = 10) -> float | None:
    expected = np.asarray([v for v in expected if v is not None], dtype=float)
    actual = np.asarray([v for v in actual if v is not None], dtype=float)
    if len(expected) < MIN_SAMPLES or len(actual) < MIN_SAMPLES:
        return None
    lo = min(expected.min(), actual.min())
    hi = max(expected.max(), actual.max())
    if hi <= lo:
        return 0.0
    edges = np.linspace(lo, hi, bins + 1)
    p = np.clip(np.histogram(expected, bins=edges)[0].astype(float), 1e-6, None)
    q = np.clip(np.histogram(actual, bins=edges)[0].astype(float), 1e-6, None)
    p /= p.sum()
    q /= q.sum()
    return round(float(np.sum(q * np.log(q / p))), 4)


def wasserstein(expected, actual) -> float | None:
    expected = [v for v in expected if v is not None]
    actual = [v for v in actual if v is not None]
    if len(expected) < MIN_SAMPLES or len(actual) < MIN_SAMPLES:
        return None
    # normalize by the baseline's scale so the score is comparable across
    # features with wildly different units (price ATR vs 0-100 RSI)
    scale = float(np.std(expected)) or 1.0
    return round(float(stats.wasserstein_distance(expected, actual)) / scale, 4)


def _feature_series(feature: str, symbol: str | None, db: Session, limit: int = 1000) -> list[float]:
    query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.asc(), PredictionFeature.id.asc())
    if symbol:
        query = query.filter(PredictionFeature.symbol == symbol)
    rows = query.limit(limit).all()
    return [
        (row.technical_features or {}).get(feature)
        for row in rows
        if row.technical_features and (row.technical_features or {}).get(feature) is not None
    ]


def check_data_drift(symbol: str | None = None, db: Session | None = None) -> dict:
    """Aggregate distribution shift across every tracked technical feature:
    mean PSI over features with enough history. One number that answers
    'does recent input data still look like what models saw before'."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        threshold = settings.max_allowed_drift
        per_feature = {}
        scores = []
        for feature in TRACKED_FEATURES:
            values = _feature_series(feature, symbol, db)
            baseline, recent = _split_window(values)
            score = population_stability_index(baseline, recent)
            per_feature[feature] = score
            if score is not None:
                scores.append(score)

        aggregate = round(float(np.mean(scores)), 4) if scores else None
        return _persist(
            "data", "PSI-mean", aggregate, threshold,
            {"symbol": symbol, "per_feature": per_feature, "features_scored": len(scores)},
            db,
        )
    finally:
        if owns_session:
            db.close()


def _resolved_hits(symbol: str | None, db: Session, limit: int = 1000) -> list[int]:
    query = (
        db.query(PredictionFeature)
        .filter(PredictionFeature.direction.in_(["LONG", "SHORT"]))
        .order_by(PredictionFeature.timestamp.asc(), PredictionFeature.id.asc())
    )
    if symbol:
        query = query.filter(PredictionFeature.symbol == symbol)
    rows = query.limit(limit).all()

    hits: list[int] = []
    prev_by_series: dict[tuple, PredictionFeature] = {}
    for row in rows:
        key = (row.symbol, row.timeframe)
        prev = prev_by_series.get(key)
        if prev is not None and prev.entry_price and row.entry_price:
            moved_up = row.entry_price > prev.entry_price
            hits.append(1 if moved_up == (prev.direction == "LONG") else 0)
        prev_by_series[key] = row
    return hits

def check_concept_drift(symbol: str | None = None, db: Session | None = None) -> dict:
    """Concept drift = the input->outcome relationship changing: measured
    as the DROP in realized directional hit rate between the baseline
    window and the recent window of directional predictions (each scored
    against the next observed price in its own symbol/timeframe series)."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        hits = _resolved_hits(symbol, db)
        baseline, recent = _split_window(hits)
        if len(baseline) < MIN_SAMPLES or len(recent) < MIN_SAMPLES:
            score = None
            details = {"symbol": symbol, "baseline_n": len(baseline), "recent_n": len(recent)}
        else:
            baseline_acc = float(np.mean(baseline))
            recent_acc = float(np.mean(recent))
            score = round(max(0.0, baseline_acc - recent_acc), 4)
            details = {
                "symbol": symbol,
                "baseline_accuracy": round(baseline_acc, 4),
                "recent_accuracy": round(recent_acc, 4),
                "baseline_n": len(baseline),
                "recent_n": len(recent),
            }
        return _persist("concept", "accuracy-drop", score, CONCEPT_DRIFT_THRESHOLD, details, db)
    finally:
        if owns_session:
            db.close()


def run_extended_scan(symbol: str = "BTCUSDT", db: Session | None = None) -> dict:
    """One full scan across all five drift types. Feature/prediction/market
    reuse the existing detectors (PSI/KS); each additionally gets a KL and
    Wasserstein reading persisted so every method has its own history."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        results = {
            "feature": check_feature_drift(symbol, db=db),
            "prediction": check_prediction_drift(symbol, db=db),
            "market": check_market_drift(symbol, db=db),
            "data": check_data_drift(symbol, db=db),
            "concept": check_concept_drift(symbol, db=db),
        }

        # KL + Wasserstein companions on the same signals as the PSI checks
        threshold = settings.max_allowed_drift
        rsi_values = _feature_series("rsi", symbol, db)
        baseline, recent = _split_window(rsi_values)
        _persist("feature", "KL", kl_divergence(baseline, recent), threshold,
                 {"symbol": symbol, "feature_name": "rsi"}, db)
        _persist("feature", "Wasserstein", wasserstein(baseline, recent), threshold,
                 {"symbol": symbol, "feature_name": "rsi"}, db)

        conf_query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.asc(), PredictionFeature.id.asc())
        if symbol:
            conf_query = conf_query.filter(PredictionFeature.symbol == symbol)
        confidences = [r.confidence for r in conf_query.limit(1000).all() if r.confidence is not None]
        c_base, c_recent = _split_window(confidences)
        _persist("prediction", "KL", kl_divergence(c_base, c_recent), threshold,
                 {"symbol": symbol, "signal": "confidence"}, db)
        _persist("prediction", "Wasserstein", wasserstein(c_base, c_recent), threshold,
                 {"symbol": symbol, "signal": "confidence"}, db)

        return results
    finally:
        if owns_session:
            db.close()


def _status(score: float | None, threshold: float | None) -> str:
    if score is None or threshold is None:
        return "no_data"
    if score <= threshold:
        return "healthy"
    if score <= threshold * 1.5:
        return "warning"
    return "critical"


def _record_dict(row: MLOpsDriftRecord) -> dict:
    return {
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "drift_type": row.drift_type,
        "method": row.method,
        "score": row.score,
        "threshold": row.threshold,
        "is_drifted": row.is_drifted,
        "status": _status(row.score, row.threshold),
        "details": row.details,
    }


def overview(db: Session | None = None) -> dict:
    """Latest reading per (drift_type, method) with status band + counts,
    for the drift dashboard cards."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        out: dict[str, dict] = {}
        for drift_type in DRIFT_TYPES:
            rows = (
                db.query(MLOpsDriftRecord)
                .filter(MLOpsDriftRecord.drift_type == drift_type)
                .order_by(MLOpsDriftRecord.checked_at.desc())
                .limit(30)
                .all()
            )
            methods: dict[str, dict] = {}
            for row in rows:
                if row.method not in methods:
                    methods[row.method] = _record_dict(row)
            out[drift_type] = {
                "methods": methods,
                "latest": _record_dict(rows[0]) if rows else None,
            }
        return out
    finally:
        if owns_session:
            db.close()


def history(days: int = 30, db: Session | None = None) -> dict:
    """Per-(type, method) daily time series for the 30-day drift charts.
    Multiple scans per day are averaged; days without a scan simply have
    no point - gaps are honest."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = (
            db.query(MLOpsDriftRecord)
            .filter(MLOpsDriftRecord.checked_at >= cutoff)
            .order_by(MLOpsDriftRecord.checked_at.asc())
            .all()
        )
        buckets: dict[tuple[str, str], dict[str, list[float]]] = {}
        thresholds: dict[tuple[str, str], float | None] = {}
        for row in rows:
            if row.score is None:
                continue
            key = (row.drift_type, row.method)
            day = row.checked_at.strftime("%Y-%m-%d")
            buckets.setdefault(key, {}).setdefault(day, []).append(row.score)
            thresholds[key] = row.threshold

        series = []
        for (drift_type, method), days_map in buckets.items():
            points = [
                {"date": day, "score": round(float(np.mean(scores)), 4)}
                for day, scores in sorted(days_map.items())
            ]
            trend = None
            if len(points) >= 2:
                trend = round(points[-1]["score"] - points[0]["score"], 4)
            series.append({
                "drift_type": drift_type,
                "method": method,
                "threshold": thresholds.get((drift_type, method)),
                "points": points,
                "trend": trend,
            })
        return {"days": days, "series": series}
    finally:
        if owns_session:
            db.close()
