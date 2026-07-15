from __future__ import annotations
import hashlib, json, uuid
from datetime import datetime, timedelta, timezone
from app.db.models import ActiveDriveDecision, SignalCandidateRecord, PredictionLedger

HORIZON_SECONDS = {"1m": 300, "3m": 900, "5m": 1500, "15m": 3600, "30m": 7200, "1h": 14400, "4h": 86400, "1d": 259200}

def persist(db, user_id: str, result: dict, reference_price: float | None, features: dict, shadow: bool = False):
    decision_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    safe_features = {k: features.get(k) for k in sorted(features) if isinstance(features.get(k), (str, int, float, bool, type(None)))}
    fingerprint = hashlib.sha256(json.dumps(safe_features, sort_keys=True, default=str).encode()).hexdigest()
    db.add(ActiveDriveDecision(decision_id=decision_id, user_id=user_id, engine=result["engine"], engine_version=result["engine_version"],
        symbol=result["symbol"], timeframe=result["timeframe"], signal=result["final_signal"], long_points=result.get("long_points", 0),
        short_points=result.get("short_points", 0), confidence=result.get("confidence") or 0, expected_edge=result.get("expected_edge"),
        eligible_for_execution=result.get("eligible_for_execution", False), blocking_reasons=result.get("blocking_reasons", []),
        decision_payload={k:v for k,v in result.items() if k != "candidates"}, shadow=shadow, created_at=now))
    horizon = HORIZON_SECONDS.get(result["timeframe"], 1500)
    for candidate in result.get("candidates", []):
        candidate_id = uuid.uuid4().hex
        record_fields = {key: candidate.get(key) for key in (
            "source_type", "source_family", "source_name", "source_version", "symbol", "timeframe", "direction",
            "probability_up", "probability_down", "confidence", "candidate_points", "expected_edge", "risk_reward_ratio",
            "market_regime", "evidence_tier", "resolved_sample_size", "historical_accuracy", "eligible",
            "rejection_reason", "evidence", "data_freshness",
        )}
        db.add(SignalCandidateRecord(id=candidate_id, decision_id=decision_id, user_id=user_id, **record_fields))
        db.add(PredictionLedger(prediction_id=uuid.uuid4().hex, candidate_id=candidate_id, decision_id=decision_id, user_id=user_id,
            engine=result["engine"], engine_version=result["engine_version"], source_type=candidate["source_type"],
            source_name=candidate["source_name"], source_version=candidate["source_version"], symbol=result["symbol"],
            timeframe=result["timeframe"], market_regime=candidate.get("market_regime"), direction=candidate["direction"],
            probability_up=candidate.get("probability_up"), probability_down=candidate.get("probability_down"), confidence=candidate.get("confidence") or 0,
            points=candidate.get("candidate_points", 0), expected_edge=candidate.get("expected_edge"), reference_price=reference_price,
            target_reference_price=result.get("recommended_target"), stop_reference_price=result.get("recommended_stop"), data_revision="active-drive-v2.1",
            target_horizon_seconds=horizon, resolution_deadline=now + timedelta(seconds=horizon), feature_snapshot_hash=fingerprint, generated_at=now))
    db.commit()
    return decision_id
