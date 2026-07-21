"""Admin-only Binance Real API credential management (Phase 32).

Replaces the ".env only" story: an admin can save, test, rotate and delete
the live-write Binance credential pair directly from the Binance Real page.

Every endpoint here:
  - requires the admin account (app.core.deps.get_current_admin)
  - requires fresh password re-authentication on save/delete (matching the
    live-unlock ceremony's pattern in app.api.trading_control)
  - is rate limited (in-process, per-admin fixed window - this deployment
    has exactly one admin account, so a shared limiter needs no per-IP
    complexity)
  - refuses plaintext-http requests in production (X-Forwarded-Proto)
  - redacts the request body from structured logs (only non-secret fields
    are ever passed to log_event/audit)
  - never returns encrypted_api_key/encrypted_api_secret or the decrypted
    values under any circumstance

Saving, rotating, testing or deleting a credential here NEVER touches
TradingControl.mode, the live-unlock lease, BINANCE_LIVE_ENABLED, or the
scheduler - see app.trading.modes for the independent gates that actually
control live order routing (Phase 31).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import credential_store
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import verify_password
from app.db.models import BinanceCredential
from app.db.session import get_db
from app.monitoring.logging import get_logger, log_event
from app.trading import modes

router = APIRouter(prefix="/api/admin/binance-credentials", tags=["admin"])
logger = get_logger("quantx.binance_credentials")

FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"  # apiRestrictions (permission detection) only exists on the spot host

# In-process fixed-window rate limit: this deployment has exactly one admin
# account, so a single shared window per action is sufficient - no new
# dependency needed for a single-operator surface.
_RATE_WINDOW_SECONDS = 60
_RATE_LIMITS = {"save": 5, "test": 10, "delete": 5}
_rate_state: dict[str, list[float]] = {}


def _rate_limit(action: str) -> None:
    now = time.time()
    window = _rate_state.setdefault(action, [])
    window[:] = [t for t in window if now - t < _RATE_WINDOW_SECONDS]
    if len(window) >= _RATE_LIMITS[action]:
        raise HTTPException(status_code=429, detail=f"Too many {action} requests - try again shortly")
    window.append(now)


def _require_secure(request: Request) -> None:
    if settings.app_env != "production":
        return
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if proto != "https":
        raise HTTPException(status_code=400, detail="Credential endpoints require HTTPS in production")


def _client_meta(request: Request) -> dict:
    return {"ip": request.client.host if request.client else None, "user_agent": request.headers.get("user-agent")}


async def _admin(request: Request, user: str = Depends(get_current_user)) -> str:
    if user != settings.admin_username:
        modes.audit("binance_credential_unauthorized_attempt", detail={"subject": user, **_client_meta(request)})
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


def _audit(event: str, admin: str, request: Request, detail: dict | None = None) -> None:
    """Never pass secret material in `detail` - every call site below only
    ever includes non-secret metadata (label, environment, fingerprint,
    validation outcome)."""
    modes.audit(event, detail={"admin": admin, **_client_meta(request), **(detail or {})})
    log_event(logger, message=event, category="risk", admin=admin)


def _row_status(row: BinanceCredential | None) -> dict:
    if row is None:
        return {
            "configured": False,
            "label": None,
            "environment": None,
            "api_key_fingerprint": None,
            "created_at": None,
            "created_by": None,
            "updated_at": None,
            "last_validated_at": None,
            "last_validation_status": None,
            "last_validation_detail": None,
            "write_permission_detected": None,
            "withdraw_enabled_detected": None,
            "credential_source": "none",
        }
    return {
        "configured": True,
        "label": row.label,
        "environment": row.environment,
        "api_key_fingerprint": row.api_key_fingerprint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_validated_at": row.last_validated_at.isoformat() if row.last_validated_at else None,
        "last_validation_status": row.last_validation_status,
        "last_validation_detail": row.last_validation_detail,
        "write_permission_detected": row.write_permission_detected,
        "withdraw_enabled_detected": row.withdraw_enabled_detected,
        "credential_source": "database_encrypted",
    }


@router.get("")
async def get_credential_status(admin: str = Depends(_admin), db: Session = Depends(get_db)):
    """Safe metadata only - never the encrypted columns, never a decrypted
    value. Also reports the independent live-routing gates (Phase 31) so
    the frontend can never conflate 'credential saved' with 'live active'."""
    row = db.get(BinanceCredential, 1)
    status = _row_status(row)
    status["encryption_store_configured"] = credential_store.store_configured()
    status["execution_mode"] = modes.effective_mode(db)
    status["message"] = "Credentials stored securely — execution remains PAPER" if row else None
    return status


class SaveCredentialRequest(BaseModel):
    password: str
    api_key: str
    api_secret: str
    label: str | None = None
    environment: str = "live"  # "live" | "testnet" - informational only


@router.post("")
async def save_credential(
    body: SaveCredentialRequest, request: Request, admin: str = Depends(_admin), db: Session = Depends(get_db)
):
    """Saves or rotates the singleton stored credential. Requires fresh
    password re-authentication - the same ceremony step used for the
    live-unlock lease. Never enables live trading, never touches
    TradingControl, never restarts the scheduler, never submits an order."""
    _require_secure(request)
    _rate_limit("save")

    if not verify_password(body.password, settings.admin_password_hash):
        _audit("binance_credential_save_rejected", admin, request, detail={"reason": "password_reauth_failed"})
        raise HTTPException(status_code=401, detail="Password re-authentication failed")
    if body.environment not in ("live", "testnet"):
        raise HTTPException(status_code=400, detail="environment must be 'live' or 'testnet'")
    api_key = body.api_key.strip()
    api_secret = body.api_secret.strip()
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Both api_key and api_secret are required")
    if not credential_store.store_configured():
        raise HTTPException(
            status_code=503,
            detail="Credential encryption is not configured on the server (CREDENTIAL_ENCRYPTION_KEY missing) - refusing to store plaintext",
        )

    row = db.get(BinanceCredential, 1)
    is_rotation = row is not None
    if row is None:
        row = BinanceCredential(id=1, created_by=admin)
        db.add(row)
    row.label = (body.label or "").strip() or None
    row.environment = body.environment
    row.encrypted_api_key = credential_store.encrypt(api_key)
    row.encrypted_api_secret = credential_store.encrypt(api_secret)
    row.api_key_fingerprint = credential_store.fingerprint(api_key)
    row.updated_at = datetime.now(timezone.utc)
    # Rotating clears any prior validation - the new pair hasn't been tested yet.
    row.last_validated_at = None
    row.last_validation_status = None
    row.last_validation_detail = None
    row.write_permission_detected = None
    row.withdraw_enabled_detected = None
    db.commit()

    _audit(
        "binance_credential_rotated" if is_rotation else "binance_credential_saved",
        admin, request, detail={"label": row.label, "environment": row.environment, "fingerprint": row.api_key_fingerprint},
    )

    return {
        "ok": True,
        "message": "Credentials stored securely — execution remains PAPER",
        "status": _row_status(row),
    }


def _sign(api_secret: str, params: dict) -> dict:
    signed = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    query = urlencode(signed)
    signed["signature"] = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return signed


async def _signed_get(base: str, path: str, api_key: str, api_secret: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{base}{path}", params=_sign(api_secret, {}), headers={"X-MBX-APIKEY": api_key})
        r.raise_for_status()
        return r.json()


def _safe_error(e: Exception) -> str:
    """Never let a raw exception (which can embed request/response bodies
    that Binance sometimes echoes) reach the client or logs verbatim."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Binance rejected the request: HTTP {e.response.status_code}"
    return f"{type(e).__name__}: could not reach Binance"


async def _test_credential(api_key: str, api_secret: str) -> dict:
    """Signed READ-ONLY account access only - /fapi/v2/account (futures
    read) and /sapi/v1/account/apiRestrictions (documented permission-
    detection endpoint, spot host). Never places, cancels, or modifies
    anything; no method here can reach a write endpoint."""
    result = {"signed_read_ok": False, "write_permission_detected": None, "withdraw_enabled_detected": None, "detail": None}
    try:
        await _signed_get(FUTURES_BASE, "/fapi/v2/account", api_key, api_secret)
        result["signed_read_ok"] = True
    except Exception as e:
        result["detail"] = _safe_error(e)
        return result

    try:
        perms = await _signed_get(SPOT_BASE, "/sapi/v1/account/apiRestrictions", api_key, api_secret)
        result["write_permission_detected"] = bool(perms.get("enableFutures") or perms.get("enableSpotAndMarginTrading"))
        result["withdraw_enabled_detected"] = bool(perms.get("enableWithdrawals"))
        result["detail"] = "Signed read succeeded; permission detection succeeded"
    except Exception as e:
        # Permission detection is a nice-to-have, not required for the
        # connection test itself to be considered a pass - the futures
        # read already proved the key/secret pair is valid and signed
        # correctly.
        result["detail"] = f"Signed read succeeded; permission detection unavailable ({_safe_error(e)})"

    return result


class TestCredentialRequest(BaseModel):
    # Optional: test an as-yet-unsaved pair (e.g. before clicking Save).
    # When omitted, tests the currently stored credential.
    api_key: str | None = None
    api_secret: str | None = None


@router.post("/test")
async def test_credential(
    body: TestCredentialRequest, request: Request, admin: str = Depends(_admin), db: Session = Depends(get_db)
):
    _require_secure(request)
    _rate_limit("test")

    if body.api_key and body.api_secret:
        api_key, api_secret = body.api_key.strip(), body.api_secret.strip()
        row = None
    else:
        row = db.get(BinanceCredential, 1)
        if row is None:
            raise HTTPException(status_code=404, detail="No stored credential to test")
        api_key = credential_store.decrypt(row.encrypted_api_key)
        api_secret = credential_store.decrypt(row.encrypted_api_secret)

    result = await _test_credential(api_key, api_secret)

    if row is not None:
        row.last_validated_at = datetime.now(timezone.utc)
        row.last_validation_status = "ok" if result["signed_read_ok"] else "failed"
        row.last_validation_detail = result["detail"]
        row.write_permission_detected = result["write_permission_detected"]
        row.withdraw_enabled_detected = result["withdraw_enabled_detected"]
        db.commit()

    _audit(
        "binance_credential_tested", admin, request,
        detail={"signed_read_ok": result["signed_read_ok"], "stored": row is not None},
    )

    return {
        "ok": result["signed_read_ok"],
        "signed_read_ok": result["signed_read_ok"],
        "write_permission_detected": result["write_permission_detected"],
        "withdraw_enabled_detected": result["withdraw_enabled_detected"],
        "detail": result["detail"],
        "status": _row_status(row) if row is not None else None,
    }


class DeleteCredentialRequest(BaseModel):
    password: str


@router.delete("")
async def delete_credential(
    body: DeleteCredentialRequest, request: Request, admin: str = Depends(_admin), db: Session = Depends(get_db)
):
    _require_secure(request)
    _rate_limit("delete")

    if not verify_password(body.password, settings.admin_password_hash):
        _audit("binance_credential_delete_rejected", admin, request, detail={"reason": "password_reauth_failed"})
        raise HTTPException(status_code=401, detail="Password re-authentication failed")

    row = db.get(BinanceCredential, 1)
    if row is None:
        raise HTTPException(status_code=404, detail="No stored credential to delete")

    fingerprint = row.api_key_fingerprint
    db.delete(row)
    db.commit()

    _audit("binance_credential_deleted", admin, request, detail={"fingerprint": fingerprint})

    return {"ok": True, "message": "Stored credential deleted. Live routing was not affected.", "status": _row_status(None)}
