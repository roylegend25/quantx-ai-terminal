"""Editable paper-trading risk limits - see app/risk/settings_repository.py
for the persistence layer and the safe bounds each field is clamped to.

Paper mode only: nothing here can enable live order placement (this
codebase has no live order-entry path at all - see app/exchanges/base.py).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.risk import settings_repository

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskSettingsUpdate(BaseModel):
    min_confidence_to_trade: float | None = Field(default=None)
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


@router.get("/settings")
async def get_risk_settings():
    return settings_repository.get_settings()


@router.put("/settings")
async def put_risk_settings(body: RiskSettingsUpdate):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No settings provided")

    try:
        return settings_repository.update_settings(patch)
    except settings_repository.InvalidRiskSetting as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/settings/reset")
async def reset_risk_settings():
    return settings_repository.reset_settings()
