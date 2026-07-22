"""Scoped Bot Settings risk limits - see app/risk/settings_repository.py for
the persistence layer and the safe bounds each field is clamped to.

Paper and Binance Real are two independent, separately-audited scopes
(`scope=paper|binance_real`), never a shared row. Nothing in this module can
enable live order placement: it never imports app.trading.modes, and a
Binance Real settings save/copy only ever changes the numeric/directional
limits an already-authorized order is checked against by
app.trading.real_risk_gate - it cannot unlock, lease, or kill-switch
anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.models import ActiveDriveDecision
from app.db.session import get_db
from app.deployment import maintenance
from app.risk import settings_repository, threshold_risk
from app.risk.settings_repository import SCOPES

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskSettingsUpdate(BaseModel):
    min_confidence_to_trade: float | None = Field(default=None)
    min_point_margin: float | None = Field(default=None)
    min_total_evidence: float | None = Field(default=None)
    max_risk_per_trade_pct: float | None = Field(default=None)
    max_daily_loss_pct: float | None = Field(default=None)
    max_weekly_loss_pct: float | None = Field(default=None)
    max_drawdown_pct: float | None = Field(default=None)
    max_consecutive_losses: int | None = Field(default=None)
    max_open_positions: int | None = Field(default=None)
    max_position_size_usd: float | None = Field(default=None)
    allow_long: bool | None = Field(default=None)
    allow_short: bool | None = Field(default=None)
    cooldown_minutes: int | None = Field(default=None)
    paper_trading_enabled: bool | None = Field(default=None)
    reason: str | None = Field(default=None)
    confirm_risk_lowering: bool = Field(default=False)


class RiskSettingsCopy(BaseModel):
    from_scope: str
    to_scope: str
    reason: str | None = Field(default=None)
    confirm: bool = Field(default=False)


class PreviewImpactRequest(BaseModel):
    scope: str
    patch: dict
    window_days: int = Field(default=14, ge=1, le=90)


def _validate_scope_param(scope: str) -> None:
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope!r}; must be one of {SCOPES}")


@router.get("/settings")
async def get_risk_settings(scope: str = "paper"):
    _validate_scope_param(scope)
    return settings_repository.get_settings(scope=scope)


@router.put("/settings")
async def put_risk_settings(body: RiskSettingsUpdate, scope: str = "paper",
                             user: str = Depends(get_current_user)):
    _validate_scope_param(scope)
    patch = {k: v for k, v in body.model_dump().items()
             if v is not None and k not in ("reason", "confirm_risk_lowering")}
    if not patch:
        raise HTTPException(status_code=400, detail="No settings provided")

    if scope == "binance_real":
        current = settings_repository.get_settings(scope=scope)
        lowered = threshold_risk.lowered_fields(patch, current)
        if lowered and not body.confirm_risk_lowering:
            raise HTTPException(status_code=409, detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Lowering confidence/point-margin/evidence requirements on Binance Real "
                           "requires explicit confirmation (confirm_risk_lowering=true).",
                "lowered_fields": lowered,
            })

    try:
        result = settings_repository.update_settings(patch, scope=scope, changed_by=user, reason=body.reason)
    except settings_repository.InvalidRiskSetting as e:
        raise HTTPException(status_code=400, detail=str(e))

    changed_thresholds = [f for f in threshold_risk.LOWER_IS_RISKIER if f in patch]
    if changed_thresholds:
        from app.decision_engine import indicator_notifications
        indicator_notifications.notify_config_threshold_changed(scope=scope, fields=changed_thresholds)
    return result


@router.post("/settings/reset")
async def reset_risk_settings(scope: str = "paper", user: str = Depends(get_current_user)):
    _validate_scope_param(scope)
    return settings_repository.reset_settings(scope=scope, changed_by=user)


@router.post("/settings/copy")
async def copy_risk_settings(body: RiskSettingsCopy, user: str = Depends(get_current_user)):
    _validate_scope_param(body.from_scope)
    _validate_scope_param(body.to_scope)
    if not body.confirm:
        raise HTTPException(status_code=409, detail={
            "code": "CONFIRMATION_REQUIRED",
            "message": f"Copying settings from {body.from_scope} to {body.to_scope} requires confirm=true. "
                       "This never enables live execution.",
        })
    try:
        result = settings_repository.copy_settings(body.from_scope, body.to_scope, changed_by=user, reason=body.reason)
    except settings_repository.InvalidScope as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.decision_engine import indicator_notifications
    indicator_notifications.notify_settings_copied(from_scope=body.from_scope, to_scope=body.to_scope)
    return result


@router.get("/settings/audit")
async def get_risk_settings_audit(scope: str = "paper", limit: int = 200):
    _validate_scope_param(scope)
    return {"scope": scope, "history": settings_repository.get_audit_history(scope=scope, limit=min(limit, 500))}


@router.get("/settings/binance-safety")
async def get_binance_safety_status(db: Session = Depends(get_db)):
    """Read-only safety section for the Binance Real tab (Bot Settings Part
    1). Composes existing read paths only - performs no writes, and cannot
    influence live-execution state."""
    from app.api.portfolio import get_read_client
    from app.trading import modes

    control = modes.get_control(db)
    mode = modes.effective_mode(db)
    maintenance_status = maintenance.status()

    positions: list = []
    open_orders: list = []
    binance_error: str | None = None
    if modes.binance_configured():
        try:
            client = get_read_client()
            positions = await client.get_positions()
            open_orders = await client.get_open_orders()
        except Exception as e:  # noqa: BLE001 - safety panel must never 500
            binance_error = str(e.__class__.__name__)

    return {
        "live_execution_status": mode,
        "server_live_lock_enabled": control.get("live_unlocked", False),
        "kill_switch_active": control.get("kill_switch_active", False),
        "maintenance": maintenance_status,
        "binance_authenticated": modes.binance_configured(),
        "binance_unavailable_reason": binance_error,
        "current_real_positions": [
            {"symbol": p.symbol, "side": p.side, "quantity": p.quantity, "entry_price": p.entry_price,
             "mark_price": p.mark_price, "unrealized_pnl": p.unrealized_pnl}
            for p in positions
        ],
        "current_real_open_orders": [
            {"symbol": o.symbol, "side": o.side, "type": o.type, "quantity": o.quantity, "price": o.price}
            for o in open_orders
        ],
    }


@router.post("/settings/preview-impact")
async def preview_settings_impact(body: PreviewImpactRequest, db: Session = Depends(get_db)):
    """Read-only impact preview (Bot Settings Part 3): runs a *proposed*
    patch against already-persisted decision/candidate history from the
    last `window_days` days, without writing anything and without calling
    into the live decision-engine evaluate() path."""
    _validate_scope_param(body.scope)
    current = settings_repository.get_settings(scope=body.scope, db=db)
    proposed = {**current, **body.patch}

    since = datetime.now(timezone.utc) - timedelta(days=body.window_days)
    decisions = (
        db.query(ActiveDriveDecision)
        .filter(ActiveDriveDecision.created_at >= since)
        .filter(ActiveDriveDecision.configuration_scope == body.scope)
        .all()
    )
    sample_size = len(decisions)

    def qualifies(d: ActiveDriveDecision, thresholds: dict) -> bool:
        margin = d.point_margin
        confidence = d.confidence
        if margin is None or confidence is None:
            return False
        return margin >= thresholds["min_point_margin"] and confidence >= thresholds["min_confidence_to_trade"]

    would_qualify_now = sum(1 for d in decisions if qualifies(d, current))
    would_qualify_proposed = sum(1 for d in decisions if qualifies(d, proposed))

    MIN_SAMPLE_FOR_RELIABLE_PREVIEW = 20
    return {
        "scope": body.scope,
        "window_days": body.window_days,
        "sample_size": sample_size,
        "sample_too_small": sample_size < MIN_SAMPLE_FOR_RELIABLE_PREVIEW,
        "current_thresholds": {k: current[k] for k in ("min_confidence_to_trade", "min_point_margin", "min_total_evidence")},
        "proposed_thresholds": {k: proposed[k] for k in ("min_confidence_to_trade", "min_point_margin", "min_total_evidence")},
        "decisions_qualifying_now": would_qualify_now,
        "decisions_qualifying_under_proposed": would_qualify_proposed,
        "signal_frequency_change": would_qualify_proposed - would_qualify_now,
    }
