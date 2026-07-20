from __future__ import annotations
import hashlib, json, uuid
from datetime import datetime, timedelta, timezone
from app.db.models import ActiveDriveDecision, PredictionCycle, SignalCandidateRecord, PredictionLedger
from app.quant.forecast import _advance_calendar_month

# Multi-bar resolution horizons for intraday timeframes (a 1m prediction is
# scored on the close ~5 bars later, not the very next bar). 1w resolves on
# the next weekly close; 1M is calendar-aware (see resolution_deadline_for)
# because "one month" is not a fixed number of seconds. 1M must never fall
# into a fixed-seconds default - the old silent 1500s fallback made weekly
# and monthly predictions "due" after 25 minutes.
HORIZON_SECONDS = {"1m": 300, "3m": 900, "5m": 1500, "15m": 3600, "30m": 7200, "1h": 14400, "4h": 86400, "1d": 259200, "1w": 604800}


def resolution_deadline_for(timeframe: str, generated_at: datetime) -> tuple[datetime, int]:
    """Deadline and horizon-seconds for one prediction. Calendar-aware for 1M."""
    if timeframe == "1M":
        deadline = datetime.fromtimestamp(
            _advance_calendar_month(int(generated_at.timestamp()), 1), tz=timezone.utc)
        return deadline, int((deadline - generated_at).total_seconds())
    seconds = HORIZON_SECONDS.get(timeframe, 1500)
    return generated_at + timedelta(seconds=seconds), seconds

def persist(db, user_id: str, result: dict, reference_price: float | None, features: dict, shadow: bool = False):
    decision_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    safe_features = {k: features.get(k) for k in sorted(features) if isinstance(features.get(k), (str, int, float, bool, type(None)))}
    fingerprint = hashlib.sha256(json.dumps(safe_features, sort_keys=True, default=str).encode()).hexdigest()
    deadline, horizon = resolution_deadline_for(result["timeframe"], now)
    active_cycle = db.query(PredictionCycle).filter(PredictionCycle.user_id == user_id).order_by(
        PredictionCycle.started_at.desc()).first()
    cycle_id = active_cycle.id if active_cycle else None
    decision_payload = {k:v for k,v in result.items() if k != "candidates"}
    decision_payload.update({
        "reference_price": reference_price,
        "target_horizon_seconds": horizon,
        "feature_snapshot_hash": fingerprint,
    })
    db.add(ActiveDriveDecision(decision_id=decision_id, user_id=user_id, engine=result["engine"], engine_version=result["engine_version"],
        symbol=result["symbol"], timeframe=result["timeframe"], signal=result["final_signal"], long_points=result.get("long_points", 0),
        short_points=result.get("short_points", 0), confidence=result.get("confidence") or 0, expected_edge=result.get("expected_edge"),
        gross_expected_edge=result.get("gross_expected_edge"), net_expected_edge=result.get("net_expected_edge"),
        edge_supported=result.get("current_edge_supported", result.get("edge_supported")), edge_block_reason=result.get("edge_block_reason"),
        edge_sample_size=result.get("edge_sample_size"), edge_source=result.get("edge_source"),
        eligible_for_execution=result.get("eligible_for_execution", False), blocking_reasons=result.get("blocking_reasons", []),
        decision_payload=decision_payload, shadow=shadow, created_at=now))
    for candidate in result.get("candidates", []):
        candidate_id = uuid.uuid4().hex
        record_fields = {key: candidate.get(key) for key in (
            "source_type", "source_family", "source_name", "source_version", "symbol", "timeframe", "direction",
            "probability_up", "probability_down", "confidence", "candidate_points", "expected_edge", "risk_reward_ratio",
            "market_regime", "evidence_tier", "resolved_sample_size", "historical_accuracy", "eligible",
            "rejection_reason", "evidence", "data_freshness",
        )}
        record_fields["evidence"] = {**(candidate.get("evidence") or {}), "diagnostics": {k: candidate.get(k) for k in ("raw_confidence","calibrated_confidence","base_points","reliability_weight","sample_size_weight","symbol_weight","timeframe_weight","regime_weight","recent_performance_weight","calibration_weight","correlation_penalty","eligible_now","regime_compatible","required_data_available","rejection_code")}}
        db.add(SignalCandidateRecord(id=candidate_id, decision_id=decision_id, user_id=user_id, **record_fields))
        db.add(PredictionLedger(prediction_id=uuid.uuid4().hex, candidate_id=candidate_id, decision_id=decision_id, user_id=user_id, cycle_id=cycle_id,
            engine=result["engine"], engine_version=result["engine_version"], source_type=candidate["source_type"],
            source_name=candidate["source_name"], source_version=candidate["source_version"], symbol=result["symbol"],
            timeframe=result["timeframe"], market_regime=candidate.get("market_regime"), direction=candidate["direction"],
            probability_up=candidate.get("probability_up"), probability_down=candidate.get("probability_down"), confidence=candidate.get("confidence") or 0,
            points=candidate.get("candidate_points", 0), expected_edge=candidate.get("expected_edge"), reference_price=reference_price,
            target_reference_price=result.get("recommended_target"), stop_reference_price=result.get("recommended_stop"), data_revision="active-drive-v2.1",
            target_horizon_seconds=horizon, resolution_deadline=deadline, feature_snapshot_hash=fingerprint, generated_at=now,
            lifecycle_status="PENDING"))
    db.commit()
    return decision_id
