from __future__ import annotations
from datetime import datetime, timezone
from app.decision_engine.repository import performance
from app.core.config import settings
from app.decision_engine.types import DecisionEngineType

FAMILY = {"trend": "trend", "momentum": "momentum", "breakout": "breakout", "mean_reversion": "mean_reversion"}

class ActiveDriveV2Engine:
    name = DecisionEngineType.ACTIVE_DRIVE_V2
    version = "2.0.0"
    min_total_evidence = settings.active_drive_min_total_evidence
    min_point_margin = settings.active_drive_min_point_margin
    min_confidence = settings.active_drive_min_confidence
    family_cap = settings.active_drive_family_cap
    def health(self): return {"status": "healthy", "failure_policy": "NO_TRADE"}
    def capabilities(self): return ["multi_source", "bounded_points", "bayesian_shrinkage", "family_caps", "append_only_ledger", "no_trade"]

    def _candidate(self, db, context, source_type, family, name, version, vote, evidence):
        direction = vote.get("direction", "NO_TRADE")
        confidence = max(0.0, min(1.0, float(vote.get("confidence") or 0) / 100.0))
        perf = performance(db, name, version, context["symbol"], context["timeframe"], context.get("regime"))
        reliability = max(0.35, min(1.0, perf["shrunk_accuracy"] / 0.5))
        sign = 1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0
        points = sign * confidence * reliability * 10.0
        if perf["resolved"] < settings.active_drive_min_resolved_samples:
            points = max(-4.0, min(4.0, points))
        p_up = 0.5 + sign * confidence / 2.0
        return {
            "source_type": source_type, "source_family": family, "source_name": name, "source_version": version,
            "symbol": context["symbol"], "timeframe": context["timeframe"], "direction": direction,
            "probability_up": round(p_up, 4), "probability_down": round(1-p_up, 4), "confidence": round(confidence, 4),
            "candidate_points": round(points, 4), "expected_edge": None, "risk_reward_ratio": context.get("risk_reward_ratio"),
            "market_regime": context.get("regime"), "evidence_tier": perf["tier"], "resolved_sample_size": perf["resolved"],
            "historical_accuracy": perf["accuracy"], "eligible": direction in ("LONG", "SHORT"),
            "rejection_reason": None if direction in ("LONG", "SHORT") else "No directional signal",
            "evidence": evidence, "data_freshness": context.get("data_status", "live"),
        }

    def evaluate(self, context: dict) -> dict:
        db = context["db"]
        legacy = context["legacy"]
        candidates = []
        for name, vote in (legacy.get("strategies") or {}).items():
            candidates.append(self._candidate(db, context, "strategy", FAMILY.get(name, "strategy"), name, "1.0.0", vote, {"reason": vote.get("reason")}))
        champ = legacy.get("ml_champion") or {}
        if champ.get("used"):
            candidates.append(self._candidate(db, context, "ml_model", "tree_ml", champ.get("model_name") or "champion_ml", champ.get("version") or "unknown", champ, {"model_id": champ.get("model_id")}))
        f = legacy.get("features") or {}
        quant_votes = [
            ("trend_strength", "quant_statistical", "LONG" if (f.get("ema20") or 0) > (f.get("ema50") or 0) else "SHORT", min(70.0, abs(float(f.get("trend_score") or 0)) * 20.0)),
            ("macd_momentum", "quant_statistical", "LONG" if (f.get("macd_hist") or 0) > 0 else "SHORT", min(65.0, abs(float(f.get("macd_hist") or 0)) * 100.0)),
            ("volatility_regime", "volatility", "NO_TRADE", 0.0),
        ]
        for name, family, direction, confidence in quant_votes:
            candidates.append(self._candidate(db, context, "quant_model", family, name, "1.0.0", {"direction": direction, "confidence": confidence}, {"features": "current_only"}))

        family_totals = {}
        for candidate in candidates:
            family = candidate["source_family"]
            family_totals[family] = family_totals.get(family, 0.0) + candidate["candidate_points"]
        family_totals = {k: max(-self.family_cap, min(self.family_cap, v)) for k, v in family_totals.items()}
        signed_total = sum(family_totals.values())
        long_points = sum(max(0.0, v) for v in family_totals.values())
        short_points = sum(max(0.0, -v) for v in family_totals.values())
        total_evidence = long_points + short_points
        margin = abs(long_points - short_points)
        confidence = min(0.99, margin / max(total_evidence, 1.0))
        blockers = []
        if context.get("data_status") == "stale": blockers.append("Market data is stale")
        if total_evidence < self.min_total_evidence: blockers.append("Insufficient total evidence")
        if margin < self.min_point_margin: blockers.append("Point margin below required threshold")
        if confidence < self.min_confidence: blockers.append("Calibrated confidence below threshold")
        blockers.append("Expected edge is not yet supported by resolved out-of-sample history")
        if context.get("risk_reward_ratio") is None: blockers.append("Risk/reward is unavailable")
        signal = "NO_TRADE" if blockers else ("LONG" if signed_total > 0 else "SHORT")
        ranked = sorted(candidates, key=lambda c: abs(c["candidate_points"]), reverse=True)
        return {
            "engine": self.name.value, "engine_version": self.version, "symbol": context["symbol"], "timeframe": context["timeframe"],
            "final_signal": signal, "confidence": round(confidence, 4), "probability_up": round(0.5 + signed_total / max(total_evidence, 1.0) * 0.5, 4),
            "probability_down": round(0.5 - signed_total / max(total_evidence, 1.0) * 0.5, 4), "expected_edge": None,
            "long_points": round(long_points, 3), "short_points": round(short_points, 3), "point_margin": round(margin, 3),
            "required_point_margin": self.min_point_margin, "total_evidence": round(total_evidence, 3),
            "eligible_for_execution": signal != "NO_TRADE" and not blockers, "blocking_reasons": blockers,
            "supporting_sources": [c for c in ranked if c["candidate_points"] > 0][:5],
            "conflicting_sources": [c for c in ranked if c["candidate_points"] < 0][:5],
            "candidates": candidates, "family_totals": family_totals,
            "market_regime": {"legacy": context.get("regime")}, "data_status": context.get("data_status", "live"),
            "evidence_tier": "insufficient_evidence" if not any(c["resolved_sample_size"] >= settings.active_drive_min_resolved_samples for c in candidates) else "early_evidence",
            "candidate_count": len(candidates), "generated_at": datetime.now(timezone.utc).isoformat(),
        }
