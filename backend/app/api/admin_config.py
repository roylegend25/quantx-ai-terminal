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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import env_manager
from app.core.config import settings
from app.core.deps import get_current_user
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
