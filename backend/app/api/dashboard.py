from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.models import ActiveDriveDecision, SignalCandidateRecord
from app.db.session import get_db
from app.decision_engine.repository import owner
from app.risk import settings_repository

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

BINANCE_FAPI = "https://fapi.binance.com"

async def symbol_snapshot(client: httpx.AsyncClient, symbol: str):
    ticker = await client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol})
    funding = await client.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
    oi = await client.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})

    ticker.raise_for_status()
    funding.raise_for_status()
    oi.raise_for_status()

    return {
        "symbol": symbol,
        "ticker": ticker.json(),
        "funding": funding.json(),
        "open_interest": oi.json(),
    }

@router.get("")
async def dashboard():
    async with httpx.AsyncClient(timeout=15) as client:
        btc = await symbol_snapshot(client, "BTCUSDT")
        eth = await symbol_snapshot(client, "ETHUSDT")

    risk = settings_repository.get_settings()

    return {
        "mode": "paper",
        "symbols": {
            "BTCUSDT": btc,
            "ETHUSDT": eth,
        },
        "bot": {
            "status": "running",
            "live_trading": False,
            "paper_trading": risk["paper_trading_enabled"],
        },
        "risk": {
            "max_risk_per_trade_pct": risk["max_risk_per_trade_pct"],
            "daily_loss_limit_pct": risk["max_daily_loss_pct"],
            "live_mode_locked": True,
        },
    }

def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()

def _requirement(metric_id: str, label: str, metric: dict, unit: str, reason_code: str | None = None) -> dict:
    value = metric.get("value")
    required = metric.get("required")
    passed = bool(metric.get("passed"))
    remaining = None
    if isinstance(value, (int, float)) and isinstance(required, (int, float)):
        remaining = max(0.0, float(required) - float(value))
    failure = metric.get("failure") or {}
    return {
        "id": metric_id, "label": label, "value": value, "required": required,
        "unit": unit, "status": "passed" if passed else ("unavailable" if value is None else "waiting"),
        "formula": metric.get("formula"), "scope": metric.get("scope"),
        "reason_code": failure.get("code") or (None if passed else reason_code),
        "explanation": failure.get("reason") or metric.get("description"),
        "remaining": remaining,
    }

@router.get("/live-decision")
def live_decision(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One bounded, user-scoped snapshot for every dashboard decision widget."""
    symbol, timeframe = symbol.upper(), timeframe.lower()
    row = (
        db.query(ActiveDriveDecision)
        .filter(
            ActiveDriveDecision.user_id == owner(current_user),
            ActiveDriveDecision.symbol == symbol,
            ActiveDriveDecision.timeframe == timeframe,
            ActiveDriveDecision.shadow.is_(False),
        )
        .order_by(ActiveDriveDecision.created_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No completed decision for this symbol/timeframe")
    payload = row.decision_payload or {}
    metrics = (payload.get("decision_metrics") or {})
    evidence = metrics.get("evidence") or {
        "value": payload.get("total_evidence"), "required": payload.get("minimum_total_evidence"),
        "passed": False, "formula": "sum(abs(family_points_after_cap))",
        "scope": f"{symbol} {timeframe} decision {row.decision_id}",
        "description": "Total usable signal strength after reliability weighting and family caps.",
    }
    margin = metrics.get("point_margin") or {
        "value": payload.get("point_margin"), "required": payload.get("required_point_margin"),
        "passed": False, "formula": "abs(long_points - short_points)",
        "scope": f"{symbol} {timeframe} decision {row.decision_id}",
        "description": "Directional lead after family caps.",
    }
    history = metrics.get("history") or {}
    confidence = metrics.get("confidence") or {}
    requirements = [
        _requirement("evidence", "Evidence", evidence, "points", "EVIDENCE_BELOW_THRESHOLD"),
        _requirement("point_margin", "Point margin", margin, "points", "POINT_MARGIN_BELOW_THRESHOLD"),
        _requirement("relevant_history", "Relevant history", history, "resolved", "INSUFFICIENT_RELEVANT_HISTORY"),
        _requirement("directional_confidence", "Directional confidence", confidence, "ratio", "DIRECTIONAL_CONFIDENCE_BELOW_THRESHOLD"),
    ]
    expected_edge = payload.get("expected_edge")
    requirements.append({
        "id": "expected_edge", "label": "Expected edge", "value": expected_edge,
        "required": None, "unit": "return", "status": "waiting" if expected_edge is None else "passed",
        "formula": "resolved out-of-sample average return after costs",
        "scope": f"{symbol} {timeframe} decision {row.decision_id}",
        "reason_code": "EDGE_NOT_SUPPORTED" if expected_edge is None else None,
        "explanation": "Requires sufficient leakage-safe resolved outcomes after costs.",
        "remaining": None,
    })
    data_status = payload.get("data_status") or {}
    requirements.append({
        "id": "data_freshness", "label": "Market data", "value": data_status.get("age_seconds"),
        "required": None, "unit": "seconds", "status": "passed" if data_status.get("fresh") else "failed",
        "formula": "latest market timestamp age", "scope": f"{symbol} {timeframe}",
        "reason_code": None if data_status.get("fresh") else "DATA_STALE",
        "explanation": "Live market data is required for an authoritative evaluation.", "remaining": None,
    })
    risk_reward = payload.get("risk_reward_ratio")
    minimum_rr = payload.get("minimum_risk_reward")
    requirements.extend([
        {
            "id": "risk_reward", "label": "Risk / reward", "value": risk_reward,
            "required": minimum_rr, "unit": "x",
            "status": "passed" if risk_reward is not None and (minimum_rr is None or risk_reward >= minimum_rr) else "waiting",
            "formula": "abs(target-reference)/abs(reference-invalidation)",
            "scope": f"{symbol} {timeframe} decision {row.decision_id}",
            "reason_code": None if risk_reward is not None else "RISK_REWARD_UNAVAILABLE",
            "explanation": "Informational forecasts do not create actionable entry, target, or stop levels.",
            "remaining": max(0.0, minimum_rr-risk_reward) if isinstance(minimum_rr,(int,float)) and isinstance(risk_reward,(int,float)) else None,
        },
        {
            "id": "regime_compatibility", "label": "Regime compatibility",
            "value": payload.get("market_regime", {}).get("label") if isinstance(payload.get("market_regime"),dict) else payload.get("market_regime"),
            "required": "validated source fit", "unit": "state", "status": "passed",
            "formula": "candidate regime weight and rejection gates",
            "scope": f"{symbol} {timeframe} decision {row.decision_id}", "reason_code": None,
            "explanation": "Incompatible sources are rejected or down-weighted before point totals.", "remaining": None,
        },
        {
            "id": "execution_safety", "label": "Execution safety",
            "value": "eligible" if row.eligible_for_execution else "blocked",
            "required": "all risk and deployment gates pass", "unit": "state",
            "status": "passed" if row.eligible_for_execution else "waiting",
            "formula": "authoritative engine + fresh decision + risk gate + live locks",
            "scope": f"user-scoped decision {row.decision_id}",
            "reason_code": None if row.eligible_for_execution else "EXECUTION_BLOCKED",
            "explanation": "Prediction and market display remain available while execution is blocked.", "remaining": None,
        },
    ])
    candidates = db.query(SignalCandidateRecord).filter(
        SignalCandidateRecord.decision_id == row.decision_id,
        SignalCandidateRecord.user_id == owner(current_user),
    ).all()
    candidate_items = [{
        "type": c.source_type, "family": c.source_family, "name": c.source_name,
        "version": c.source_version, "direction": c.direction, "points": c.candidate_points,
        "eligible": c.eligible, "evidence_tier": c.evidence_tier,
        "resolved_history": c.resolved_sample_size,
        "reason": (c.evidence or {}).get("reason") or c.rejection_reason,
    } for c in candidates]
    supporting = sorted((c for c in candidate_items if c["points"] > 0), key=lambda c: abs(c["points"]), reverse=True)[:8]
    conflicting = sorted((c for c in candidate_items if c["points"] < 0), key=lambda c: abs(c["points"]), reverse=True)[:8]
    family_totals = {}
    for group, source_types in {
        "ml": {"ml", "ml_model"}, "strategies": {"strategy"}, "quant": {"quant", "quant_model"},
    }.items():
        members = [c for c in candidate_items if c["type"] in source_types and c["eligible"]]
        family_totals[group] = {
            "long": round(sum(c["points"] for c in members if c["points"] > 0), 4),
            "short": round(abs(sum(c["points"] for c in members if c["points"] < 0)), 4),
            "eligible": len(members),
        }
    history_rows = (
        db.query(ActiveDriveDecision)
        .filter(
            ActiveDriveDecision.user_id == owner(current_user),
            ActiveDriveDecision.symbol == symbol,
            ActiveDriveDecision.timeframe == timeframe,
            ActiveDriveDecision.engine == row.engine,
            ActiveDriveDecision.shadow.is_(False),
        )
        .order_by(ActiveDriveDecision.created_at.desc())
        .limit(120)
        .all()
    )
    decision_history = []
    for item in reversed(history_rows):
        p = item.decision_payload or {}
        decision_history.append({
            "timestamp": _iso(item.created_at), "decision_id": item.decision_id, "signal": item.signal,
            "long_points": item.long_points, "short_points": item.short_points,
            "point_margin": p.get("point_margin", abs(item.long_points-item.short_points)),
            "required_point_margin": p.get("required_point_margin"),
            "evidence": p.get("total_evidence"), "required_evidence": p.get("minimum_total_evidence"),
            "directional_confidence": p.get("directional_confidence"),
            "required_confidence": p.get("required_confidence"),
            "execution_eligible": item.eligible_for_execution,
            "primary_blocker": (item.blocking_reasons or [None])[0],
        })
    return {
        "symbol": symbol, "timeframe": timeframe,
        "engine": {"id": row.engine, "name": "Active Drive V2" if row.engine == "active_drive_v2" else "Active Drive V1", "version": row.engine_version},
        "decision_id": row.decision_id, "generated_at": _iso(row.created_at),
        "next_evaluation_at": _iso(row.created_at + timedelta(seconds=10)),
        "market_data_revision": payload.get("market_data_revision"),
        "performance_snapshot_revision": payload.get("performance_snapshot_revision"),
        "signal": row.signal, "execution_eligible": row.eligible_for_execution,
        "long_points": row.long_points, "short_points": row.short_points,
        "point_margin": margin.get("value"), "evidence": evidence.get("value"),
        "directional_confidence": payload.get("directional_confidence"),
        "abstention_confidence": payload.get("abstention_confidence"),
        "expected_edge": row.expected_edge, "market_regime": payload.get("market_regime") or {},
        "requirements": requirements, "blocking_reasons": row.blocking_reasons or [],
        "top_supporting_sources": supporting, "top_conflicting_sources": conflicting,
        "source_family_totals": family_totals,
        "candidate_counts": {
            "total": len(candidate_items),
            "eligible": sum(bool(c["eligible"]) for c in candidate_items),
            "ml": sum(c["type"] in ("ml", "ml_model") for c in candidate_items),
            "strategy": sum(c["type"] == "strategy" for c in candidate_items),
            "quant": sum(c["type"] in ("quant", "quant_model") for c in candidate_items),
        },
        "forecast": payload.get("forecast") or {"available": False, "trade_actionable": False, "reason": "Use the matching prediction forecast snapshot"},
        "data_status": data_status, "decision_history": decision_history,
    }
