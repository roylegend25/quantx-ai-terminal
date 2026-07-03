"""Feature/prediction/market drift detection for the ML lifecycle platform.

Two statistical methods, both computed on plain numeric samples:
  - Population Stability Index (PSI): standard 10-bin distribution-shift
    score, binned off the baseline ("expected") sample's quantiles.
  - Kolmogorov-Smirnov two-sample test (scipy.stats.ks_2samp).

Each check compares a recent window against an older baseline window of the
same signal and persists an MLOpsDriftRecord row. Read-only against every
other subsystem - drift checks never touch live predictions or trades.
"""

from datetime import datetime, timezone

import numpy as np
from scipy import stats
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MLOpsDriftRecord, PredictionFeature
from app.db.session import SessionLocal
from app.mlops.feature_store import store as feature_store

PSI_BINS = 10
MIN_SAMPLES = 10


def population_stability_index(expected, actual, bins: int = PSI_BINS) -> float | None:
    expected = np.asarray([v for v in expected if v is not None], dtype=float)
    actual = np.asarray([v for v in actual if v is not None], dtype=float)
    if len(expected) < MIN_SAMPLES or len(actual) < MIN_SAMPLES:
        return None

    quantiles = np.linspace(0, 100, bins + 1)
    edges = np.unique(np.percentile(expected, quantiles))
    if len(edges) < 3:
        return None
    edges[0], edges[-1] = -np.inf, np.inf

    expected_counts = np.histogram(expected, bins=edges)[0].astype(float)
    actual_counts = np.histogram(actual, bins=edges)[0].astype(float)

    expected_pct = np.clip(expected_counts / expected_counts.sum(), 1e-6, None)
    actual_pct = np.clip(actual_counts / actual_counts.sum(), 1e-6, None)

    psi = float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))
    return round(psi, 4)


def ks_test(expected, actual) -> dict | None:
    expected = [v for v in expected if v is not None]
    actual = [v for v in actual if v is not None]
    if len(expected) < MIN_SAMPLES or len(actual) < MIN_SAMPLES:
        return None

    result = stats.ks_2samp(expected, actual)
    return {"statistic": round(float(result.statistic), 4), "p_value": round(float(result.pvalue), 4)}


def _split_window(values: list, recent_frac: float = 0.3) -> tuple[list, list]:
    """Oldest-first list -> (baseline, recent), recent being the tail
    `recent_frac` of the series."""
    n = len(values)
    cut = max(1, int(n * (1 - recent_frac)))
    return values[:cut], values[cut:]


def _persist(drift_type: str, method: str, score: float | None, threshold: float, details: dict, db: Session) -> dict:
    is_drifted = bool(score is not None and score > threshold)
    row = MLOpsDriftRecord(
        checked_at=datetime.now(timezone.utc),
        drift_type=drift_type,
        method=method,
        score=score,
        threshold=threshold,
        is_drifted=is_drifted,
        details=details,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


def _row_to_dict(row: MLOpsDriftRecord) -> dict:
    return {
        "id": row.id,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "drift_type": row.drift_type,
        "method": row.method,
        "score": row.score,
        "threshold": row.threshold,
        "is_drifted": row.is_drifted,
        "details": row.details,
    }


def check_feature_drift(
    symbol: str,
    feature_name: str = "rsi",
    feature_version: str | None = None,
    method: str = "PSI",
    db: Session | None = None,
) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        snapshots = feature_store.get_snapshots(symbol=symbol, feature_version=feature_version, limit=200, db=db)
        values = [s["features"].get(feature_name) for s in reversed(snapshots) if s.get("features")]
        baseline, recent = _split_window(values)

        threshold = settings.max_allowed_drift
        if method == "KS":
            result = ks_test(baseline, recent)
            score = result["statistic"] if result else None
        else:
            method = "PSI"
            score = population_stability_index(baseline, recent)

        return _persist(
            "feature",
            method,
            score,
            threshold,
            {"symbol": symbol, "feature_name": feature_name, "baseline_n": len(baseline), "recent_n": len(recent)},
            db,
        )
    finally:
        if owns_session:
            db.close()


def check_prediction_drift(symbol: str | None = None, method: str = "PSI", db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.asc())
        if symbol:
            query = query.filter(PredictionFeature.symbol == symbol)
        values = [row.confidence for row in query.limit(1000).all()]
        baseline, recent = _split_window(values)

        threshold = settings.max_allowed_drift
        if method == "KS":
            result = ks_test(baseline, recent)
            score = result["statistic"] if result else None
        else:
            method = "PSI"
            score = population_stability_index(baseline, recent)

        return _persist(
            "prediction",
            method,
            score,
            threshold,
            {"symbol": symbol, "signal": "confidence", "baseline_n": len(baseline), "recent_n": len(recent)},
            db,
        )
    finally:
        if owns_session:
            db.close()


def check_market_drift(symbol: str | None = None, method: str = "KS", db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.asc())
        if symbol:
            query = query.filter(PredictionFeature.symbol == symbol)
        rows = query.limit(1000).all()
        values = [
            (row.technical_features or {}).get("realized_volatility")
            for row in rows
            if row.technical_features
        ]
        baseline, recent = _split_window(values)

        threshold = settings.max_allowed_drift
        if method == "PSI":
            score = population_stability_index(baseline, recent)
        else:
            method = "KS"
            result = ks_test(baseline, recent)
            score = result["statistic"] if result else None

        return _persist(
            "market",
            method,
            score,
            threshold,
            {"symbol": symbol, "signal": "realized_volatility", "baseline_n": len(baseline), "recent_n": len(recent)},
            db,
        )
    finally:
        if owns_session:
            db.close()


def run_full_scan(symbol: str = "BTCUSDT", db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        return {
            "feature": check_feature_drift(symbol, db=db),
            "prediction": check_prediction_drift(symbol, db=db),
            "market": check_market_drift(symbol, db=db),
        }
    finally:
        if owns_session:
            db.close()


def latest_by_type(db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        result = {}
        for drift_type in ("feature", "prediction", "market"):
            row = (
                db.query(MLOpsDriftRecord)
                .filter(MLOpsDriftRecord.drift_type == drift_type)
                .order_by(MLOpsDriftRecord.checked_at.desc())
                .first()
            )
            result[drift_type] = _row_to_dict(row) if row else None
        return result
    finally:
        if owns_session:
            db.close()
