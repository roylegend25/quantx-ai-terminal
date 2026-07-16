from __future__ import annotations
from app.core.config import settings
from app.decision_engine.repository import get_setting, owner
from app.decision_engine.types import DecisionEngineType
from app.decision_engine.v1 import ActiveDriveV1Adapter
from app.decision_engine.v2 import ActiveDriveV2Engine

class DecisionEngineRouter:
    def __init__(self):
        self.engines = {DecisionEngineType.ACTIVE_DRIVE_V1: ActiveDriveV1Adapter(), DecisionEngineType.ACTIVE_DRIVE_V2: ActiveDriveV2Engine()}
    def get_active_engine(self, db, user_id):
        selected = DecisionEngineType(get_setting(db, user_id).decision_engine)
        return self.engines[selected]
    def evaluate(self, db, user_id, context):
        engine = self.get_active_engine(db, user_id)
        try:
            return engine.evaluate({**context, "db": db, "user_id": owner(user_id)})
        except Exception as exc:
            return {"engine": engine.name.value, "engine_info":{"id":engine.name.value,"name":"Active Drive V2" if engine.name==DecisionEngineType.ACTIVE_DRIVE_V2 else "Active Drive V1","version":engine.version,"authoritative":True}, "engine_version": engine.version, "symbol": context["symbol"], "timeframe": context["timeframe"],
                    "signal":"NO_TRADE", "final_signal": "NO_TRADE", "confidence": None, "directional_confidence":None, "decision_confidence":None, "abstention_confidence":1.0, "probability_up": 0.5, "probability_down": 0.5,
                    "expected_edge": None, "long_points": 0.0, "short_points": 0.0, "point_margin": 0.0,
                    "required_confidence":settings.active_drive_min_confidence,"minimum_total_evidence":settings.active_drive_min_total_evidence,
                    "required_point_margin": settings.active_drive_min_point_margin, "eligible_for_execution": False,
                    "blocking_reasons": [f"Active Drive V2 failed closed: {type(exc).__name__}"], "supporting_sources": [],
                    "conflicting_sources": [], "candidates": [], "market_regime": {}, "data_status": "stale",
                    "evidence_tier": "insufficient_evidence", "candidate_count": 0, "health": "error"}
    def assert_authoritative(self, db, user_id, decision):
        active = self.get_active_engine(db, user_id)
        if decision.get("engine") != active.name.value or decision.get("engine_version") != active.version:
            raise ValueError("ENGINE_NOT_ACTIVE")
        if not decision.get("eligible_for_execution"):
            raise ValueError("DECISION_NOT_EXECUTION_ELIGIBLE")

decision_engine_router = DecisionEngineRouter()
