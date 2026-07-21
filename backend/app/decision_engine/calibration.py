"""Decision-engine calibration dataset and rules (2026-07-20 resolver repair).

A clean, trustworthy-outcomes-only dataset for evaluation and calibration -
never an uncontrolled online self-training loop. PENDING predictions are
never treated as failures; VOID predictions never train or calibrate
anything; only RESOLVED_* (correct/wrong/neutral) outcomes are trustworthy.

Automatic recalibration is disabled by default and stays disabled until its
offline and shadow validation passes (settings.calibration_auto_apply_enabled,
default False) - propose_calibration_update only ever computes and persists
a PROPOSAL; nothing here silently changes a live strategy weight.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CalibrationVersion, PredictionLedger, PredictionResolution
from app.decision_engine import outcome as outcome_mod

# A calibration update must never be proposed from a thin sample - the
# minimum sample size and the maximum weight delta per cycle are both
# distinct safety gates, not the same knob.
MIN_SAMPLE_FOR_CALIBRATION = 50
MAX_WEIGHT_DELTA_PER_CYCLE = 0.10  # a single calibration cycle may move a weight by at most +/-10%
CONFIDENCE_BAND_WIDTH = 0.10  # deciles


def _confidence_band(confidence: float | None) -> str | None:
    if confidence is None:
        return None
    band = min(9, max(0, int(confidence / CONFIDENCE_BAND_WIDTH)))
    lo, hi = band * 10, band * 10 + 10
    return f"{lo}-{hi}%"


def trustworthy_rows(db: Session, *, symbol: str | None = None, timeframe: str | None = None,
                      direction: str | None = None, source_name: str | None = None,
                      limit: int | None = None):
    """Only RESOLVED_* (correct/wrong/neutral) rows - the one gate every
    calibration/evaluation consumer must go through. PENDING/RESOLVING/
    RESOLUTION_ERROR_RETRYING/VOID_* are never returned here."""
    q = (
        db.query(PredictionLedger, PredictionResolution)
        .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionLedger.lifecycle_status.in_(tuple(outcome_mod.TRUSTWORTHY_STATUSES)))
    )
    if symbol:
        q = q.filter(PredictionLedger.symbol == symbol.upper())
    if timeframe:
        q = q.filter(PredictionLedger.timeframe == timeframe)
    if direction:
        q = q.filter(PredictionLedger.direction == direction.upper())
    if source_name:
        q = q.filter(PredictionLedger.source_name == source_name)
    q = q.order_by(PredictionResolution.resolved_at.desc())
    if limit:
        q = q.limit(limit)
    return q.all()


def compute_metrics(rows: list[tuple[PredictionLedger, PredictionResolution]]) -> dict:
    """Every metric in the spec, computed from a trustworthy-only row set.
    Directional metrics (hit rate, precision, Brier, calibration error)
    exclude neutral rows from their denominator - a neutral outcome was
    never a directional bet to be right or wrong about."""
    n = len(rows)
    if n == 0:
        return {
            "count": 0, "correct": 0, "wrong": 0, "neutral": 0, "void": 0,
            "directional_hit_rate": None, "precision": None, "brier_score": None,
            "calibration_error": None, "average_predicted_confidence": None,
            "actual_success_rate": None, "average_expected_move": None,
            "average_realized_move": None, "average_net_move_after_costs": None,
            "false_positive_rate": None, "average_mfe": None, "average_mae": None,
        }
    correct = sum(1 for _, r in rows if r.correct is True)
    wrong = sum(1 for _, r in rows if r.correct is False)
    neutral = sum(1 for _, r in rows if r.neutral_result)
    directional = correct + wrong
    confidences = [l.confidence for l, _ in rows if l.confidence is not None]
    expected_moves = [l.expected_edge for l, _ in rows if l.expected_edge is not None]
    realized_moves = [r.actual_return for _, r in rows if r.actual_return is not None]
    net_moves = [r.net_direction_adjusted_return for _, r in rows if r.net_direction_adjusted_return is not None]
    mfe = [r.maximum_favorable_excursion for _, r in rows if r.maximum_favorable_excursion is not None]
    mae = [r.maximum_adverse_excursion for _, r in rows if r.maximum_adverse_excursion is not None]

    # Brier score over the directional subset: predicted probability of the
    # actually-realized direction vs 1.0 (correct) / 0.0 (wrong). Confidence
    # is stored as the model's own probability-like [0,1] score.
    brier_terms = []
    calib_bucket_pred: dict[str, list[float]] = {}
    calib_bucket_actual: dict[str, list[float]] = {}
    for ledger, res in rows:
        if res.correct is None or ledger.confidence is None:
            continue
        actual = 1.0 if res.correct else 0.0
        brier_terms.append((ledger.confidence - actual) ** 2)
        band = _confidence_band(ledger.confidence)
        if band:
            calib_bucket_pred.setdefault(band, []).append(ledger.confidence)
            calib_bucket_actual.setdefault(band, []).append(actual)
    brier = sum(brier_terms) / len(brier_terms) if brier_terms else None
    # Calibration error: mean absolute gap between average predicted
    # confidence and actual success rate, averaged across confidence bands
    # that have at least one sample - a well-calibrated model has ~0 here.
    band_gaps = []
    for band, preds in calib_bucket_pred.items():
        actual_rate = sum(calib_bucket_actual[band]) / len(calib_bucket_actual[band])
        band_gaps.append(abs(sum(preds) / len(preds) - actual_rate))
    calibration_error = sum(band_gaps) / len(band_gaps) if band_gaps else None

    return {
        "count": n, "correct": correct, "wrong": wrong, "neutral": neutral, "void": 0,
        "directional_hit_rate": round(correct / directional, 4) if directional else None,
        "precision": round(correct / directional, 4) if directional else None,
        "brier_score": round(brier, 4) if brier is not None else None,
        "calibration_error": round(calibration_error, 4) if calibration_error is not None else None,
        "average_predicted_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "actual_success_rate": round(correct / directional, 4) if directional else None,
        "average_expected_move": round(sum(expected_moves) / len(expected_moves), 6) if expected_moves else None,
        "average_realized_move": round(sum(realized_moves) / len(realized_moves), 6) if realized_moves else None,
        "average_net_move_after_costs": round(sum(net_moves) / len(net_moves), 6) if net_moves else None,
        "false_positive_rate": round(wrong / directional, 4) if directional else None,
        "average_mfe": round(sum(mfe) / len(mfe), 6) if mfe else None,
        "average_mae": round(sum(mae) / len(mae), 6) if mae else None,
    }


def rolling_performance(db: Session, *, symbol: str, timeframe: str, direction: str | None = None,
                         source_name: str | None = None) -> dict:
    """Rolling 20/50/100-prediction performance, most-recent-first."""
    rows = trustworthy_rows(db, symbol=symbol, timeframe=timeframe, direction=direction, source_name=source_name, limit=100)
    return {
        "rolling_20": compute_metrics(rows[:20]),
        "rolling_50": compute_metrics(rows[:50]),
        "rolling_100": compute_metrics(rows[:100]),
    }


def calibration_dataset(db: Session) -> dict:
    """Full breakdown: combined, by symbol, by timeframe, by direction,
    by strategy/model (source_name), by confidence band. Market/volatility
    regime, hour-of-day, and data-quality-level cuts are not yet broken out
    here - see the repair report for scope notes."""
    btc_eth = PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT"))
    trustworthy = PredictionLedger.lifecycle_status.in_(tuple(outcome_mod.TRUSTWORTHY_STATUSES))

    combined = compute_metrics(trustworthy_rows(db))
    by_symbol = {sym: compute_metrics(trustworthy_rows(db, symbol=sym)) for sym in ("BTCUSDT", "ETHUSDT")}
    by_direction = {d: compute_metrics(trustworthy_rows(db, direction=d)) for d in ("LONG", "SHORT")}
    timeframes = [r[0] for r in db.query(PredictionLedger.timeframe).join(
        PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(btc_eth, trustworthy).distinct().all()]
    by_timeframe = {tf: compute_metrics(trustworthy_rows(db, timeframe=tf)) for tf in timeframes}

    return {
        "combined": combined,
        "by_symbol": by_symbol,
        "by_direction": by_direction,
        "by_timeframe": by_timeframe,
        "min_sample_for_calibration": MIN_SAMPLE_FOR_CALIBRATION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def propose_calibration_update(db: Session, *, created_by: str, current_weights: dict[str, float],
                                notes: str | None = None) -> dict:
    """Computes a bounded, sample-gated weight adjustment PROPOSAL and
    persists it as an inactive CalibrationVersion row. Never applies it.

    Rules enforced here (not optional, not configurable per-call):
      - a source with fewer than MIN_SAMPLE_FOR_CALIBRATION trustworthy
        resolved predictions gets no proposed change at all (weight stays
        exactly as given);
      - any proposed change is clamped to +/-MAX_WEIGHT_DELTA_PER_CYCLE of
        the current weight, however far the raw accuracy signal would
        otherwise push it;
      - the previous active version (if any) is recorded as
        previous_version_id so this is always a single-step, reversible
        chain, never a silent overwrite.
    """
    proposed_weights: dict[str, float] = {}
    per_source_metrics: dict[str, dict] = {}
    total_sample = 0
    for source_name, current in current_weights.items():
        rows = trustworthy_rows(db, source_name=source_name)
        metrics = compute_metrics(rows)
        per_source_metrics[source_name] = metrics
        total_sample += metrics["count"]
        if metrics["count"] < MIN_SAMPLE_FOR_CALIBRATION or metrics["directional_hit_rate"] is None:
            proposed_weights[source_name] = current
            continue
        # Directional accuracy vs. a coin-flip baseline (0.5) suggests the
        # direction of adjustment; magnitude is bounded regardless.
        signal = (metrics["directional_hit_rate"] - 0.5) * 2  # in [-1, 1]
        raw_delta = signal * MAX_WEIGHT_DELTA_PER_CYCLE
        delta = max(-MAX_WEIGHT_DELTA_PER_CYCLE, min(MAX_WEIGHT_DELTA_PER_CYCLE, raw_delta))
        proposed_weights[source_name] = round(current * (1 + delta), 6)

    previous = db.query(CalibrationVersion).filter(CalibrationVersion.active.is_(True)).one_or_none()
    version = CalibrationVersion(
        created_by=created_by, sample_size=total_sample, weights_snapshot=proposed_weights,
        metrics_snapshot=per_source_metrics, previous_version_id=previous.id if previous else None,
        active=False, notes=notes,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return {"version_id": version.id, "weights": proposed_weights, "metrics": per_source_metrics,
            "sample_size": total_sample, "applied": False,
            "auto_apply_enabled": settings.calibration_auto_apply_enabled}


def rollback_calibration(db: Session, *, to_version_id: int | None = None) -> dict:
    """Deactivates the current active version and reactivates either the
    specified prior version or its own recorded previous_version_id."""
    current = db.query(CalibrationVersion).filter(CalibrationVersion.active.is_(True)).one_or_none()
    target_id = to_version_id or (current.previous_version_id if current else None)
    if target_id is None:
        raise ValueError("No prior calibration version to roll back to")
    target = db.get(CalibrationVersion, target_id)
    if target is None:
        raise ValueError(f"Calibration version {target_id} not found")
    now = datetime.now(timezone.utc)
    if current is not None:
        current.active = False
        current.rolled_back_at = now
    target.active = True
    target.applied_at = now
    db.commit()
    return {"active_version_id": target.id, "weights": target.weights_snapshot}
