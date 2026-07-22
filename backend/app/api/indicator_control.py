"""Indicator Performance Control - read/action API for the Predictions
page's "Indicator Performance" section (Bot Settings Part 7) and the
governance-threshold settings (Part 6/10).

Manual reactivation for scope="binance_real" only ever sets
IndicatorEligibility(mode="binance_real").status = "ACTIVE" - this module
never imports app.trading.modes and cannot unlock, lease, or otherwise
enable live execution, structurally, not just behaviorally.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.models import IndicatorEligibility, IndicatorEligibilityHistory, IndicatorPerformanceRollup
from app.db.session import get_db
from app.decision_engine import governance_repository, indicator_notifications
from app.decision_engine.eligibility import (
    STATUS_ACTIVE,
    STATUS_MANUALLY_DISABLED,
)

router = APIRouter(prefix="/api/indicators", tags=["indicators"])

MODES = ("paper", "binance_real")
ACTIONS = ("enable_paper", "enable_binance_real", "enable_both", "keep_shadow", "disable")


def _eligibility_to_dict(row: IndicatorEligibility) -> dict:
    return {
        "id": row.id, "source_name": row.source_name, "source_version": row.source_version,
        "symbol": row.symbol, "timeframe": row.timeframe, "mode": row.mode, "status": row.status,
        "status_reason": row.status_reason, "trigger_snapshot": row.trigger_snapshot,
        "shadow_since": row.shadow_since.isoformat() if row.shadow_since else None,
        "last_evaluated_at": row.last_evaluated_at.isoformat() if row.last_evaluated_at else None,
        "last_status_change_at": row.last_status_change_at.isoformat() if row.last_status_change_at else None,
        "evaluation_version": row.evaluation_version,
        "starred": row.status == "RECOMMENDED_FOR_REACTIVATION",
    }


def _rollup_to_dict(row: IndicatorPerformanceRollup | None) -> dict:
    if row is None:
        return {
            "sample_size": 0, "correct": 0, "wrong": 0, "neutral": 0, "wrong_rate": None,
            "hit_rate": None, "net_expectancy": None, "mfe_avg": None, "mae_avg": None,
            "last_10_outcomes": [], "data_quality_flag": False, "computed_at": None,
        }
    return {
        "sample_size": row.sample_size, "correct": row.correct, "wrong": row.wrong, "neutral": row.neutral,
        "wrong_rate": row.wrong_rate, "hit_rate": row.hit_rate, "net_expectancy": row.net_expectancy,
        "mfe_avg": row.mfe_avg, "mae_avg": row.mae_avg, "last_10_outcomes": row.last_10_outcomes or [],
        "data_quality_flag": row.data_quality_flag, "computed_at": row.computed_at.isoformat() if row.computed_at else None,
    }


@router.get("/performance")
async def get_indicator_performance(symbol: str | None = None, timeframe: str | None = None,
                                     status: str | None = None, db: Session = Depends(get_db)):
    """Per-Part-7 combined view: one row per (indicator, symbol, timeframe,
    mode), joining eligibility status with active/shadow performance
    rollups."""
    query = db.query(IndicatorEligibility)
    if symbol:
        query = query.filter(IndicatorEligibility.symbol == symbol.upper())
    if timeframe:
        query = query.filter(IndicatorEligibility.timeframe == timeframe)
    if status:
        query = query.filter(IndicatorEligibility.status == status)
    eligibility_rows = query.order_by(IndicatorEligibility.source_name, IndicatorEligibility.symbol,
                                       IndicatorEligibility.timeframe).all()

    rollups = {
        (r.source_name, r.source_version, r.symbol, r.timeframe, r.execution_mode): r
        for r in db.query(IndicatorPerformanceRollup).all()
    }

    results = []
    for row in eligibility_rows:
        active_rollup = rollups.get((row.source_name, row.source_version, row.symbol, row.timeframe, "ACTIVE"))
        shadow_rollup = rollups.get((row.source_name, row.source_version, row.symbol, row.timeframe, "SHADOW"))
        results.append({
            **_eligibility_to_dict(row),
            "active_performance": _rollup_to_dict(active_rollup),
            "shadow_performance": _rollup_to_dict(shadow_rollup),
            "current_ensemble_influence": "none" if row.status in ("SHADOW_ONLY_POOR_PERFORMANCE", "RECOMMENDED_FOR_REACTIVATION", "MANUALLY_DISABLED") else "active",
        })
    return {"indicators": results, "count": len(results)}


@router.get("/{eligibility_id}/history")
async def get_indicator_history(eligibility_id: int, db: Session = Depends(get_db)):
    row = db.get(IndicatorEligibility, eligibility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Indicator eligibility record not found")
    history = (
        db.query(IndicatorEligibilityHistory)
        .filter_by(eligibility_id=eligibility_id)
        .order_by(IndicatorEligibilityHistory.created_at.desc())
        .all()
    )
    return {
        "current": _eligibility_to_dict(row),
        "history": [
            {
                "id": h.id, "previous_status": h.previous_status, "new_status": h.new_status,
                "trigger_snapshot": h.trigger_snapshot, "changed_by": h.changed_by, "reason": h.reason,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ],
    }


class IndicatorActionRequest(BaseModel):
    action: str
    reason: str | None = Field(default=None)


@router.post("/{eligibility_id}/action")
async def apply_indicator_action(eligibility_id: int, body: IndicatorActionRequest,
                                  user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action!r}; must be one of {ACTIONS}")

    row = db.get(IndicatorEligibility, eligibility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Indicator eligibility record not found")

    now = datetime.now(timezone.utc)
    targets = [row]
    if body.action == "enable_both":
        sibling_mode = "binance_real" if row.mode == "paper" else "paper"
        sibling = (
            db.query(IndicatorEligibility)
            .filter_by(source_name=row.source_name, source_version=row.source_version,
                       symbol=row.symbol, timeframe=row.timeframe, mode=sibling_mode)
            .first()
        )
        if sibling is not None:
            targets.append(sibling)

    new_status = {
        "enable_paper": STATUS_ACTIVE, "enable_binance_real": STATUS_ACTIVE, "enable_both": STATUS_ACTIVE,
        "keep_shadow": None, "disable": STATUS_MANUALLY_DISABLED,
    }[body.action]

    updated = []
    for target in targets:
        if body.action == "enable_paper" and target.mode != "paper":
            continue
        if body.action == "enable_binance_real" and target.mode != "binance_real":
            continue
        if new_status is None:
            # keep_shadow: no status change, just an audited acknowledgement.
            db.add(IndicatorEligibilityHistory(
                eligibility_id=target.id, source_name=target.source_name, source_version=target.source_version,
                symbol=target.symbol, timeframe=target.timeframe, mode=target.mode,
                previous_status=target.status, new_status=target.status,
                trigger_snapshot={"action": "keep_shadow"}, changed_by=user, reason=body.reason, created_at=now,
            ))
            db.commit()
            updated.append(target)
            continue
        if target.status == new_status:
            continue
        previous_status = target.status
        target.status = new_status
        target.status_reason = f"Manual action '{body.action}' by {user}"
        target.last_status_change_at = now
        target.evaluation_version = (target.evaluation_version or 0) + 1
        db.add(IndicatorEligibilityHistory(
            eligibility_id=target.id, source_name=target.source_name, source_version=target.source_version,
            symbol=target.symbol, timeframe=target.timeframe, mode=target.mode,
            previous_status=previous_status, new_status=new_status, trigger_snapshot={"action": body.action},
            changed_by=user, reason=body.reason, created_at=now,
        ))
        db.commit()
        db.refresh(target)
        updated.append(target)

    return {"updated": [_eligibility_to_dict(t) for t in updated]}


@router.get("/notifications")
async def list_indicator_notifications(limit: int = 50, unread_only: bool = False, db: Session = Depends(get_db)):
    return indicator_notifications.list_notifications(limit=limit, unread_only=unread_only, db=db)


@router.post("/notifications/{notification_id}/read")
async def mark_indicator_notification_read(notification_id: int, db: Session = Depends(get_db)):
    row = indicator_notifications.mark_read(notification_id, db=db)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return row


@router.post("/notifications/read-all")
async def mark_all_indicator_notifications_read(db: Session = Depends(get_db)):
    return {"updated": indicator_notifications.mark_all_read(db=db)}


@router.get("/governance-settings")
async def get_governance_settings(db: Session = Depends(get_db)):
    return governance_repository.get_settings(db=db)


class GovernanceSettingsUpdate(BaseModel):
    poor_performance_window: int | None = None
    poor_performance_wrong_threshold: int | None = None
    min_sample_for_poor_performance_check: int | None = None
    status_change_cooldown_hours: float | None = None
    star_min_shadow_samples: int | None = None
    star_min_hit_rate: float | None = None
    star_max_wrong_rate: float | None = None
    star_max_mae_pct: float | None = None
    star_recent_subwindow: int | None = None
    data_quality_void_rate_threshold: float | None = None


@router.put("/governance-settings")
async def put_governance_settings(body: GovernanceSettingsUpdate, db: Session = Depends(get_db)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No settings provided")
    try:
        return governance_repository.update_settings(patch, db=db)
    except governance_repository.InvalidGovernanceSetting as e:
        raise HTTPException(status_code=400, detail=str(e))
