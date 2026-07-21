"""Admin-only server trading configuration (Phase 24).

Controlled panel over the deployment's .env for the small allowlist in
app/core/env_manager.py - NOT a .env editor. Secrets are never readable or
writable through this surface: responses only ever say "configured yes/no",
and the env manager structurally refuses every non-allowlisted key.

Authorization: every route requires the admin account itself
(app/core/deps.get_current_admin) - a valid non-admin token gets 403, and
the failed attempt is audited.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import env_manager
from app.core.config import settings
from app.core.deps import get_current_user
from app.db.models import ActiveDriveDecision
from app.db.session import get_db
from app.monitoring.logging import get_logger, log_event
from app.trading import modes

router = APIRouter(prefix="/api/admin/server-config", tags=["admin"])

logger = get_logger("quantx.admin_config")

# The server-lock ceremony's acknowledgement keys (distinct from the user
# live-unlock ceremony in app/trading/modes.py - both must be completed
# before a real order is possible).
SERVER_LOCK_ACKNOWLEDGEMENTS = (
    "real_money",
    "withdrawals_disabled",
    "ip_whitelisted",
    "loss_possible",
    "risk_limits_checked",
)

# Leverage above this needs the explicit allow_high_leverage flag.
SOFT_MAX_LEVERAGE = 3.0
HARD_MAX_LEVERAGE = 20.0


def _client_meta(request: Request) -> dict:
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _audit(event: str, admin: str, request: Request, detail: dict | None = None) -> None:
    modes.audit(event, detail={"admin": admin, **_client_meta(request), **(detail or {})})


async def _admin(request: Request, user: str = Depends(get_current_user)) -> str:
    """get_current_admin's rule, plus auditing: a valid non-admin token is
    recorded as an unauthorized attempt before the 403 propagates."""
    if user != settings.admin_username:
        modes.audit(
            "server_config_unauthorized_attempt",
            detail={"subject": user, **_client_meta(request)},
        )
        log_event(
            logger,
            message="server_config_unauthorized_attempt",
            level=logging.WARNING,
            category="risk",
            subject=user,
        )
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


def _safe_config() -> dict:
    control = modes.get_control()
    return {
        "active_mode": modes.effective_mode(),
        "binance_live_enabled": settings.binance_live_enabled,
        "binance_api_key_configured": bool(settings.binance_api_key),
        "binance_api_secret_configured": bool(settings.binance_api_secret),
        "binance_live_unlocked_by_user": control["live_unlocked"],
        "kill_switch_active": control["kill_switch_active"],
        "default_leverage": settings.binance_default_leverage,
        "max_leverage": settings.binance_max_leverage,
        "max_notional_per_trade": settings.binance_max_notional_per_trade,
        "max_daily_loss_usdt": settings.binance_max_daily_loss_usdt,
        "allowed_symbols": settings.binance_allowed_symbols,
        "active_drive_min_confidence": settings.active_drive_min_confidence,
        "active_drive_min_confidence_floor": env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR,
        # edits apply to the live settings object immediately AND persist in
        # .env; a container recreate simply re-reads the same file
        "restart_required": False,
        "env_file": None,  # path intentionally not disclosed
    }


@router.get("")
async def get_server_config(admin: str = Depends(_admin)):
    return _safe_config()


class BinanceLiveRequest(BaseModel):
    enabled: bool
    typed_confirmation: str = ""
    acknowledgements: dict[str, bool] = {}


@router.post("/binance-live")
async def set_binance_live(body: BinanceLiveRequest, request: Request, admin: str = Depends(_admin)):
    """Flip the SERVER half of the live lock (BINANCE_LIVE_ENABLED).
    Enabling demands the typed phrase + every acknowledgement; disabling is
    deliberately friction-free and takes effect immediately - real trading
    degrades to LOCKED on the very next check."""
    if body.enabled:
        if body.typed_confirmation.strip() != modes.LIVE_UNLOCK_PHRASE:
            raise HTTPException(status_code=400, detail=f'Type exactly "{modes.LIVE_UNLOCK_PHRASE}" to confirm')
        missing = [k for k in SERVER_LOCK_ACKNOWLEDGEMENTS if not body.acknowledgements.get(k)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"All acknowledgements must be checked (missing: {', '.join(missing)})",
            )
        if not (settings.binance_api_key and settings.binance_api_secret):
            raise HTTPException(status_code=400, detail="Binance API keys are not configured on the server")

    try:
        changes = env_manager.update_env_file({"BINANCE_LIVE_ENABLED": body.enabled})
    except env_manager.EnvUpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=500, detail="Could not write the server configuration file")

    env_manager.apply_to_settings()

    _audit(
        "server_live_lock_enabled" if body.enabled else "server_live_lock_disabled",
        admin, request, detail={"changes": changes},
    )

    return {
        "ok": True,
        "message": (
            "Server live trading permission ENABLED. The user live-risk confirmation is still "
            "required before real orders can be placed."
            if body.enabled
            else "Server live trading permission disabled - Binance real trading is locked."
        ),
        "config": _safe_config(),
        "status": modes.exchange_safe_status(),
    }


class RiskLimitsRequest(BaseModel):
    BINANCE_DEFAULT_LEVERAGE: float | None = None
    BINANCE_MAX_LEVERAGE: float | None = None
    BINANCE_MAX_NOTIONAL_PER_TRADE: float | None = None
    BINANCE_MAX_DAILY_LOSS_USDT: float | None = None
    BINANCE_ALLOWED_SYMBOLS: str | list[str] | None = None
    # explicit escape hatch required to configure leverage above 3x
    allow_high_leverage: bool = False


@router.patch("/risk-limits")
async def update_risk_limits(body: RiskLimitsRequest, request: Request, admin: str = Depends(_admin)):
    updates = {
        k: v
        for k, v in body.model_dump(exclude={"allow_high_leverage"}).items()
        if v is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    max_leverage = updates.get("BINANCE_MAX_LEVERAGE", settings.binance_max_leverage)
    default_leverage = updates.get("BINANCE_DEFAULT_LEVERAGE", settings.binance_default_leverage)
    try:
        max_leverage = float(max_leverage)
        default_leverage = float(default_leverage)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Leverage values must be numbers")
    if max_leverage > HARD_MAX_LEVERAGE:
        raise HTTPException(status_code=400, detail=f"Max leverage cannot exceed {HARD_MAX_LEVERAGE:g}x")
    if max_leverage > SOFT_MAX_LEVERAGE and not body.allow_high_leverage:
        raise HTTPException(
            status_code=400,
            detail=f"Max leverage above {SOFT_MAX_LEVERAGE:g}x requires allow_high_leverage=true",
        )
    if default_leverage > max_leverage:
        raise HTTPException(status_code=400, detail="Default leverage cannot exceed max leverage")

    try:
        changes = env_manager.update_env_file(updates)
    except env_manager.EnvUpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=500, detail="Could not write the server configuration file")

    env_manager.apply_to_settings()

    _audit("server_risk_limits_changed", admin, request, detail={"changes": changes})

    return {"ok": True, "message": "Risk limits updated and applied", "config": _safe_config()}


def _classify_confidence_risk(value: float) -> str:
    """Descriptive banding only - not a statistical claim about win rate.
    Bands start at the hard floor itself, since nothing below it is ever
    reachable through this API."""
    if value < env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR:
        return "BELOW_FLOOR"
    if value < 0.65:
        return "AGGRESSIVE"
    if value < 0.75:
        return "MODERATE"
    if value < 0.85:
        return "CONSERVATIVE"
    return "VERY_CONSERVATIVE"


_HISTORY_LOOKBACK_DAYS = 30


def _confidence_history_stats(db: Session, proposed: float) -> dict:
    """Real counts from ActiveDriveDecision (app/decision_engine/ledger.py
    persists one row per decision cycle, non-shadow, with the actual
    calibrated directional confidence used at gate time) over the last
    _HISTORY_LOOKBACK_DAYS - never an estimate, and explicitly reports zero
    history rather than fabricating a rate when none exists yet."""
    window_start = datetime.now(timezone.utc) - timedelta(days=_HISTORY_LOOKBACK_DAYS)
    rows = (
        db.query(ActiveDriveDecision.confidence)
        .filter(
            ActiveDriveDecision.shadow.is_(False),
            ActiveDriveDecision.created_at >= window_start,
            ActiveDriveDecision.confidence.isnot(None),
        )
        .all()
    )
    total = len(rows)
    if total == 0:
        return {
            "lookback_days": _HISTORY_LOOKBACK_DAYS,
            "total_decisions": 0,
            "would_pass_at_current_threshold": None,
            "would_pass_at_proposed_threshold": None,
            "note": "No decision history in this window yet - stats will populate as the engine runs.",
        }
    current_threshold = settings.active_drive_min_confidence
    pass_current = sum(1 for (c,) in rows if c >= current_threshold)
    pass_proposed = sum(1 for (c,) in rows if c >= proposed)
    return {
        "lookback_days": _HISTORY_LOOKBACK_DAYS,
        "total_decisions": total,
        "current_threshold": current_threshold,
        "would_pass_at_current_threshold": pass_current,
        "would_pass_at_current_threshold_pct": round(100 * pass_current / total, 1),
        "proposed_threshold": proposed,
        "would_pass_at_proposed_threshold": pass_proposed,
        "would_pass_at_proposed_threshold_pct": round(100 * pass_proposed / total, 1),
    }


@router.get("/confidence-threshold/preview")
async def preview_confidence_threshold(proposed: float, db: Session = Depends(get_db), admin: str = Depends(_admin)):
    """Read-only: shows the risk classification and real historical impact
    of a candidate threshold WITHOUT changing anything - lets an admin see
    the effect before committing via the PATCH endpoint below."""
    if proposed < env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR or proposed > 1.0:
        raise HTTPException(
            status_code=400,
            detail=f"proposed must be between {env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR:g} and 1.0",
        )
    return {
        "proposed_threshold": proposed,
        "floor": env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR,
        "risk_classification": _classify_confidence_risk(proposed),
        "history": _confidence_history_stats(db, proposed),
    }


class ConfidenceThresholdRequest(BaseModel):
    min_confidence: float


@router.patch("/confidence-threshold")
async def update_confidence_threshold(
    body: ConfidenceThresholdRequest, request: Request, db: Session = Depends(get_db), admin: str = Depends(_admin)
):
    """Changes only the GATE threshold that a decision's own calibrated
    directional confidence must clear - never the confidence value itself,
    which is always computed by the decision engine from real evidence.
    Enforces the hard institutional floor (env_manager.
    ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR) unconditionally; every change is
    audited with the historical impact computed at change time."""
    try:
        changes = env_manager.update_env_file({"ACTIVE_DRIVE_MIN_CONFIDENCE": body.min_confidence})
    except env_manager.EnvUpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=500, detail="Could not write the server configuration file")

    env_manager.apply_to_settings()
    history = _confidence_history_stats(db, body.min_confidence)
    classification = _classify_confidence_risk(body.min_confidence)

    _audit(
        "server_confidence_threshold_changed",
        admin, request,
        detail={"changes": changes, "risk_classification": classification, "history": history},
    )

    return {
        "ok": True,
        "message": f"Calibrated directional confidence threshold set to {body.min_confidence:g} ({classification}).",
        "risk_classification": classification,
        "history": history,
        "config": _safe_config(),
    }


@router.post("/reload")
async def reload_config(request: Request, admin: str = Depends(_admin)):
    """Re-apply the .env file's allowed keys onto the running settings. The
    allowed keys apply live; anything else in .env (keys, DB URLs...) needs
    a container recreate: `cd ~/quantx-ai-terminal && docker compose up -d
    --build backend`."""
    applied = env_manager.apply_to_settings()
    _audit("server_config_reload", admin, request, detail={"applied": applied})
    return {
        "ok": True,
        "restart_required": False,
        "message": "Trading settings reloaded from the configuration file. "
        "Other variables (API keys, database) require a backend restart: "
        "cd ~/quantx-ai-terminal && docker compose up -d --build backend",
        "applied": applied,
        "config": _safe_config(),
    }
