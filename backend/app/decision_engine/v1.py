from datetime import datetime, timezone
from app.decision_engine.types import DecisionEngineType

class ActiveDriveV1Adapter:
    name = DecisionEngineType.ACTIVE_DRIVE_V1
    version = "1.0.0"
    def health(self): return {"status": "healthy"}
    def capabilities(self): return ["legacy_strategy_ensemble", "optional_champion_gate"]
    def evaluate(self, context: dict) -> dict:
        legacy = context["legacy"]
        direction = legacy.get("direction", "NO_TRADE")
        return {
            "engine": self.name.value, "engine_version": self.version,
            "symbol": context["symbol"], "timeframe": context["timeframe"],
            "final_signal": direction, "confidence": (legacy.get("confidence") or 0) / 100,
            "probability_up": (legacy.get("probability_up") or 0) / 100,
            "probability_down": (legacy.get("probability_down") or 0) / 100,
            "expected_edge": None, "long_points": 0.0, "short_points": 0.0,
            "point_margin": 0.0, "required_point_margin": None,
            "eligible_for_execution": bool(legacy.get("risk", {}).get("allowed")),
            "blocking_reasons": [] if legacy.get("risk", {}).get("allowed") else [legacy.get("risk", {}).get("reason", "Legacy risk gate blocked")],
            "supporting_sources": [], "conflicting_sources": [],
            "market_regime": {"legacy": legacy.get("regime")},
            "data_status": "stale" if legacy.get("features", {}).get("stale") else "live",
            "evidence_tier": "legacy_unscoped", "candidate_count": 1,
            "candidates": [{"source_type":"meta_model", "source_family":"legacy", "source_name":"active_drive_v1", "source_version":self.version,
                "symbol":context["symbol"], "timeframe":context["timeframe"], "direction":direction,
                "probability_up":(legacy.get("probability_up") or 0)/100, "probability_down":(legacy.get("probability_down") or 0)/100,
                "confidence":(legacy.get("confidence") or 0)/100, "candidate_points":0.0, "expected_edge":None, "risk_reward_ratio":context.get("risk_reward_ratio"),
                "market_regime":legacy.get("regime"), "evidence_tier":"legacy_unscoped", "resolved_sample_size":0, "historical_accuracy":None,
                "eligible":direction in ("LONG","SHORT"), "rejection_reason":None if direction in ("LONG","SHORT") else "No directional signal",
                "evidence":{"legacy":True}, "data_freshness":context.get("data_status","live")}],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
